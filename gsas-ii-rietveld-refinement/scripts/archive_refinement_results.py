#!/usr/bin/env python3
"""Transactionally archive validated GSAS-II refinement deliverables."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refinement_audit import default_data_root

DEFAULT_ARCHIVE = default_data_root(
    "GSASII_REFINEMENT_ARCHIVE", "GSAS-II_refinement_results"
)
DEFAULT_STAGING_ROOT = default_data_root(
    "GSASII_REFINEMENT_STAGING", "GSAS-II_refinement_staging"
)

REQUIRED_ROLES = {
    "result_cif",
    "source_cif",
    "instrument",
    "xrd",
    "gpx",
    "lst",
    "report",
    "candidate_summary",
    "report_validation",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_name(value: str) -> str:
    output = []
    for character in value.strip():
        if character.isalnum() or character in "-_.":
            output.append(character)
        elif character in " /\\:;,+()[]{}":
            output.append("_")
    cleaned = "".join(output).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "unnamed"


def require_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label} is not a file: {path}")
    if path.stat().st_size <= 0:
        raise SystemExit(f"{label} is empty: {path}")
    return path


def is_within(path: Path, parent: Path) -> bool:
    resolved_path = path.resolve()
    resolved_parent = parent.resolve()
    return resolved_path == resolved_parent or resolved_parent in resolved_path.parents


def validate_no_archive_staging_overlap(
    archive_root: Path,
    dest_dir: Path,
    staging_root: Path,
    staging_dir: Path | None,
) -> None:
    if is_within(archive_root, staging_root) or is_within(staging_root, archive_root):
        raise SystemExit(
            "Archive root and staging root must not contain one another: "
            f"{archive_root} vs {staging_root}"
        )
    if staging_dir is not None:
        if not is_within(staging_dir, staging_root) or staging_dir == staging_root:
            raise SystemExit(
                f"Staging directory must be a child of staging root: {staging_dir}"
            )
        if is_within(dest_dir, staging_dir) or is_within(staging_dir, dest_dir):
            raise SystemExit(
                "Final archive and cleanup target must not contain one another: "
                f"{dest_dir} vs {staging_dir}"
            )


def fsync_file(path: Path) -> None:
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_verified(
    source: Path,
    destination: Path,
    manifest: dict[str, Any],
    role: str,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256(source)
    shutil.copy2(source, destination)
    if not destination.is_file() or destination.stat().st_size <= 0:
        raise RuntimeError(f"Copy verification failed: {destination}")
    destination_hash = sha256(destination)
    if destination_hash != source_hash:
        raise RuntimeError(
            f"Hash mismatch after copying {source} to {destination}"
        )
    fsync_file(destination)
    manifest["files"].append(
        {
            "role": role,
            "archive_name": destination.name,
            "source_path": str(source),
            "source_sha256": source_hash,
            "bytes": destination.stat().st_size,
            "sha256": destination_hash,
        }
    )


def write_manifest(directory: Path, manifest: dict[str, Any]) -> Path:
    path = directory / "manifest.json"
    temporary = directory / ".manifest.json.tmp"
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fsync_file(temporary)
    temporary.replace(path)
    fsync_directory(directory)
    return path


def verify_transaction(
    directory: Path, manifest: dict[str, Any]
) -> None:
    roles = {item["role"] for item in manifest["files"]}
    missing = REQUIRED_ROLES - roles
    if missing:
        raise RuntimeError(
            "Transaction is missing required archive roles: "
            + ", ".join(sorted(missing))
        )
    for item in manifest["files"]:
        path = directory / item["archive_name"]
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise RuntimeError(f"Archived file missing or size changed: {path}")
        if sha256(path) != item["sha256"]:
            raise RuntimeError(f"Archived file hash changed: {path}")
    validation_items = [
        item for item in manifest["files"] if item["role"] == "report_validation"
    ]
    validation_path = directory / validation_items[0]["archive_name"]
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "pass":
        raise RuntimeError(
            "Report validation status is not pass; refusing final archive"
        )
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size <= 0:
        raise RuntimeError("Manifest was not written")


def atomic_install(
    temporary_dir: Path,
    destination: Path,
    *,
    replace: bool,
) -> None:
    parent = destination.parent
    backup: Path | None = None
    if destination.exists():
        if not replace:
            raise SystemExit(
                f"Archive already exists. Use --replace to update: {destination}"
            )
        backup = parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
        destination.replace(backup)
    try:
        temporary_dir.replace(destination)
        fsync_directory(parent)
    except Exception:
        if backup is not None and backup.exists() and not destination.exists():
            backup.replace(destination)
            fsync_directory(parent)
        raise
    if backup is not None:
        shutil.rmtree(backup)
        fsync_directory(parent)


def safe_remove_staging(staging_dir: Path, staging_root: Path) -> None:
    staging = staging_dir.resolve()
    root = staging_root.resolve()
    if staging == root or root not in staging.parents:
        raise RuntimeError(
            f"Refusing to remove staging outside allowed child path: {staging}"
        )
    if staging.exists():
        shutil.rmtree(staging)


def update_cleanup_status(
    destination: Path,
    *,
    status: str,
    error: str | None = None,
) -> None:
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["cleanup"]["status"] = status
    manifest["cleanup"]["completed_at"] = datetime.now(timezone.utc).isoformat()
    if error:
        manifest["cleanup"]["error"] = error
    write_manifest(destination, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--cif-key", required=True)
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Atomically replace an existing final archive after validation",
    )
    parser.add_argument("--cleanup-staging", action="store_true")
    parser.add_argument("--staging-dir")
    parser.add_argument("--source-cif", required=True)
    parser.add_argument("--result-cif", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--xrd", required=True)
    parser.add_argument("--gpx", required=True)
    parser.add_argument("--lst", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--candidate-summary", required=True)
    parser.add_argument("--report-validation", required=True)
    parser.add_argument("--extra", action="append", default=[])
    args = parser.parse_args()

    # Validate every argument before creating, deleting, or renaming an archive.
    if args.cleanup_staging and not args.staging_dir:
        raise SystemExit("--cleanup-staging requires --staging-dir")
    inputs = {
        "result_cif": require_file(args.result_cif, "result CIF"),
        "source_cif": require_file(args.source_cif, "source CIF"),
        "instrument": require_file(args.instrument, "instrument parameter file"),
        "xrd": require_file(args.xrd, "XRD data"),
        "gpx": require_file(args.gpx, "GPX"),
        "lst": require_file(args.lst, "LST"),
        "report": require_file(args.report, "report"),
        "candidate_summary": require_file(
            args.candidate_summary, "candidate summary"
        ),
        "report_validation": require_file(
            args.report_validation, "report validation"
        ),
    }
    extras = [require_file(value, "extra") for value in args.extra]
    validation = json.loads(
        inputs["report_validation"].read_text(encoding="utf-8")
    )
    if validation.get("status") != "pass":
        raise SystemExit(
            "Report validation must have status=pass before archiving"
        )
    expected_report_hash = validation.get("report", {}).get("sha256")
    expected_summary_hash = validation.get("candidate_summary", {}).get("sha256")
    if expected_report_hash != sha256(inputs["report"]):
        raise SystemExit(
            "Report hash does not match report_validation.json"
        )
    if expected_summary_hash != sha256(inputs["candidate_summary"]):
        raise SystemExit(
            "Candidate-summary hash does not match report_validation.json"
        )
    summary = json.loads(inputs["candidate_summary"].read_text(encoding="utf-8"))
    if summary.get("selected_candidate") != validation.get("selected_candidate"):
        raise SystemExit(
            "Selected candidate differs between summary and report validation"
        )
    for role, summary_role in (
        ("source_cif", "cif"),
        ("instrument", "instrument"),
        ("xrd", "xrd"),
    ):
        expected = summary.get("inputs", {}).get(summary_role, {}).get("sha256")
        if expected != sha256(inputs[role]):
            raise SystemExit(
                f"{role} hash does not match candidate_summary.json"
            )
    for role in ("result_cif", "gpx", "lst"):
        expected = summary.get("selected_files", {}).get(role, {}).get("sha256")
        if expected != sha256(inputs[role]):
            raise SystemExit(
                f"{role} hash does not match selected_files in candidate_summary.json"
            )

    sample = clean_name(args.sample_id)
    cif_key = clean_name(args.cif_key)
    archive_root = Path(args.archive_root).expanduser().resolve()
    staging_root = Path(args.staging_root).expanduser().resolve()
    destination = archive_root / cif_key / sample
    staging_dir = (
        Path(args.staging_dir).expanduser().resolve()
        if args.staging_dir
        else None
    )
    validate_no_archive_staging_overlap(
        archive_root, destination, staging_root, staging_dir
    )
    if destination.exists() and not args.replace:
        raise SystemExit(
            f"Archive already exists. Use --replace to update: {destination}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{sample}.transaction-",
            dir=str(destination.parent),
        )
    )
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sample_id": args.sample_id,
        "cif_key": args.cif_key,
        "archive_dir": str(destination),
        "transaction": {
            "strategy": "validate-copy-hash-atomic-replace",
            "replace_requested": bool(args.replace),
        },
        "files": [],
        "cleanup": {
            "requested": bool(args.cleanup_staging),
            "staging_dir": str(staging_dir) if staging_dir else None,
            "status": "pending" if args.cleanup_staging else "not_requested",
        },
    }
    names = {
        "result_cif": f"{sample}_result.cif",
        "source_cif": f"{sample}_source_model.cif",
        "instrument": f"{sample}_instrument{inputs['instrument'].suffix or '.prm'}",
        "xrd": f"{sample}_xrd{inputs['xrd'].suffix or '.txt'}",
        "gpx": f"{sample}_refinement.gpx",
        "lst": f"{sample}_refinement.lst",
        "report": f"{sample}_report.md",
        "candidate_summary": f"{sample}_candidate_summary.json",
        "report_validation": f"{sample}_report_validation.json",
    }
    try:
        for role, source in inputs.items():
            copy_verified(source, temporary_dir / names[role], manifest, role)
        for index, source in enumerate(extras, start=1):
            copy_verified(
                source,
                temporary_dir
                / f"{sample}_extra_{index:02d}_{clean_name(source.name)}",
                manifest,
                "extra",
            )
        write_manifest(temporary_dir, manifest)
        verify_transaction(temporary_dir, manifest)
        atomic_install(temporary_dir, destination, replace=args.replace)
    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir)
        raise

    # Cleanup happens only after the final archive has been installed and
    # independently reverified.
    final_manifest = json.loads(
        (destination / "manifest.json").read_text(encoding="utf-8")
    )
    verify_transaction(destination, final_manifest)
    if args.cleanup_staging and staging_dir is not None:
        try:
            safe_remove_staging(staging_dir, staging_root)
            update_cleanup_status(destination, status="completed")
        except Exception as exc:
            update_cleanup_status(
                destination,
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
