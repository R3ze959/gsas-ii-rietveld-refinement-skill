#!/usr/bin/env python3
"""Archive final GSAS-II refinement deliverables and optionally clean staging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ARCHIVE = Path(os.environ.get("GSASII_REFINEMENT_ARCHIVE", "~/GSAS-II_refinement_results")).expanduser()
DEFAULT_STAGING_ROOT = Path(os.environ.get("GSASII_REFINEMENT_STAGING", "~/GSAS-II_refinement_staging")).expanduser()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def clean_name(value: str) -> str:
    keep = []
    for ch in value.strip():
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        elif ch in " /\\:;,+()[]{}":
            keep.append("_")
    cleaned = "".join(keep).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "unnamed"


def require_file(path: str | None, label: str, required: bool = False) -> Path | None:
    if not path:
        if required:
            raise SystemExit(f"Missing required {label}")
        return None
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise SystemExit(f"{label} is not a file: {p}")
    if p.stat().st_size <= 0:
        raise SystemExit(f"{label} is empty: {p}")
    return p


def copy_item(src: Path, dest: Path, manifest: dict, role: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise SystemExit(f"Copy verification failed for {dest}")
    manifest["files"].append(
        {
            "role": role,
            "archive_name": dest.name,
            "archive_path": str(dest),
            "source_path": str(src),
            "bytes": dest.stat().st_size,
            "sha256": sha256(dest),
        }
    )


def safe_remove_staging(staging_dir: Path) -> None:
    staging = staging_dir.expanduser().resolve()
    root = DEFAULT_STAGING_ROOT.resolve()
    if staging == root:
        raise SystemExit(f"Refusing to remove staging root itself: {staging}")
    if root not in staging.parents:
        raise SystemExit(f"Refusing to remove staging outside allowed root: {staging}")
    if staging.exists():
        shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--cif-key", required=True, help="Classification folder, e.g. Nb14W3O44_I4m_2405347")
    parser.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE))
    parser.add_argument("--replace", action="store_true", help="Replace an existing final archive for this sample/CIF key")
    parser.add_argument("--cleanup-staging", action="store_true")
    parser.add_argument("--staging-dir")
    parser.add_argument("--source-cif")
    parser.add_argument("--result-cif")
    parser.add_argument("--xrd", required=True)
    parser.add_argument("--gpx")
    parser.add_argument("--lst")
    parser.add_argument("--plot", action="append", default=[])
    parser.add_argument("--report")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--artifact", action="append", default=[], help="Final artifacts copied with their original file names, e.g. Python plot outputs and CSV tables")
    args = parser.parse_args()

    sample = clean_name(args.sample_id)
    cif_key = clean_name(args.cif_key)
    archive_root = Path(args.archive_root).expanduser().resolve()
    dest_dir = archive_root / cif_key / sample

    if dest_dir.exists():
        if not args.replace:
            raise SystemExit(f"Archive already exists. Use --replace to update: {dest_dir}")
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    result_cif = require_file(args.result_cif, "result CIF")
    source_cif = require_file(args.source_cif, "source CIF")
    xrd = require_file(args.xrd, "XRD data", required=True)
    gpx = require_file(args.gpx, "GPX")
    lst = require_file(args.lst, "LST")
    report = require_file(args.report, "report")
    plots = [require_file(p, "plot", required=True) for p in args.plot]
    extras = [require_file(p, "extra", required=True) for p in args.extra]
    artifacts = [require_file(p, "artifact", required=True) for p in args.artifact]

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sample_id": args.sample_id,
        "cif_key": args.cif_key,
        "archive_dir": str(dest_dir),
        "files": [],
        "cleanup": {"requested": bool(args.cleanup_staging), "staging_dir": args.staging_dir},
    }

    if result_cif:
        copy_item(result_cif, dest_dir / f"{sample}_result.cif", manifest, "result_cif")
    if source_cif:
        copy_item(source_cif, dest_dir / f"{sample}_source_model.cif", manifest, "source_cif")
    copy_item(xrd, dest_dir / f"{sample}_xrd{xrd.suffix or '.txt'}", manifest, "xrd")
    if gpx:
        copy_item(gpx, dest_dir / f"{sample}_refinement.gpx", manifest, "gpx")
    if lst:
        copy_item(lst, dest_dir / f"{sample}_refinement.lst", manifest, "lst")
    if report:
        copy_item(report, dest_dir / f"{sample}_report.md", manifest, "report")
    for idx, plot in enumerate(plots, 1):
        suffix = plot.suffix or ".png"
        copy_item(plot, dest_dir / f"{sample}_fit_{idx}{suffix}", manifest, "plot")
    for extra in extras:
        copy_item(extra, dest_dir / f"{sample}_extra_{clean_name(extra.name)}", manifest, "extra")
    for artifact in artifacts:
        copy_item(artifact, dest_dir / clean_name(artifact.name), manifest, "artifact")

    manifest_path = dest_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.cleanup_staging:
        if not args.staging_dir:
            raise SystemExit("--cleanup-staging requires --staging-dir")
        safe_remove_staging(Path(args.staging_dir))
        manifest["cleanup"]["completed"] = True
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(dest_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
