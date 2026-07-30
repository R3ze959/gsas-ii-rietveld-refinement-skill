#!/usr/bin/env python3
"""Safely materialize one audited candidate as the selected final files."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from refinement_audit import (
    candidate_safety_errors,
    resolve_gsasii_path,
    sha256,
    write_json_atomic,
)


def clean_name(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in value.strip()
    ).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "unnamed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-summary", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--gsasii-path",
        help="GSAS-II source tree; defaults to GSASII_DIR or common locations",
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    summary_path = Path(args.candidate_summary).expanduser().resolve()
    if not summary_path.is_file():
        raise SystemExit(f"Candidate summary not found: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    matches = [
        item
        for item in summary.get("candidates", [])
        if item.get("name") == args.candidate
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"Candidate is absent or duplicated: {args.candidate}"
        )
    candidate = matches[0]
    safety_errors = candidate_safety_errors(candidate)
    if safety_errors:
        raise SystemExit(
            "Candidate failed the automatic safety gate: "
            + "; ".join(safety_errors)
        )
    source_gpx = Path(candidate["gpx"]["path"]).expanduser().resolve()
    source_lst = source_gpx.with_suffix(".lst")
    for path, label in ((source_gpx, "candidate GPX"), (source_lst, "candidate LST")):
        if not path.is_file() or path.stat().st_size <= 0:
            raise SystemExit(f"{label} is missing or empty: {path}")
    if sha256(source_gpx) != candidate["gpx"]["sha256"]:
        raise SystemExit("Candidate GPX hash changed after candidate_summary.json")

    run_dir = Path(summary["run_dir"]).expanduser().resolve()
    if summary_path.parent != run_dir:
        raise SystemExit(
            "candidate_summary.json must be located directly in its run_dir"
        )
    selected_dir = run_dir / "selected"
    if selected_dir.exists():
        if not args.replace:
            raise SystemExit(
                f"Selected directory exists; use --replace: {selected_dir}"
            )
    backup = run_dir / ".selected.previous"
    if backup.exists():
        raise SystemExit(f"Selection rollback path already exists: {backup}")
    temporary = run_dir / ".selected.transaction"
    if temporary.exists():
        raise SystemExit(f"Selection transaction already exists: {temporary}")

    gsasii_path = resolve_gsasii_path(args.gsasii_path)
    if str(gsasii_path) not in sys.path:
        sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIscriptable as G2sc  # type: ignore

    temporary.mkdir()
    sample = clean_name(summary["sample_id"])
    final_gpx = temporary / f"{sample}_refinement.gpx"
    final_lst = temporary / f"{sample}_refinement.lst"
    final_cif = temporary / f"{sample}_result.cif"
    try:
        shutil.copy2(source_gpx, final_gpx)
        shutil.copy2(source_lst, final_lst)
        project = G2sc.G2Project(str(final_gpx))
        if len(project.phases()) != 1:
            raise RuntimeError(
                "Deterministic selection requires exactly one phase"
            )
        project.phases()[0].export_CIF(str(final_cif))
        for path in (final_gpx, final_lst, final_cif):
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError(f"Selected output is missing or empty: {path}")
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    had_previous = selected_dir.exists()
    if had_previous:
        selected_dir.replace(backup)
    try:
        temporary.replace(selected_dir)
        selected_paths = {
            "gpx": selected_dir / final_gpx.name,
            "lst": selected_dir / final_lst.name,
            "result_cif": selected_dir / final_cif.name,
        }
        summary["selected_candidate"] = args.candidate
        summary["selected_files"] = {
            role: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for role, path in selected_paths.items()
        }
        write_json_atomic(summary_path, summary)
    except Exception:
        if selected_dir.exists():
            selected_dir.replace(temporary)
        if had_previous and backup.exists():
            backup.replace(selected_dir)
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    if backup.exists():
        shutil.rmtree(backup)
    print(selected_dir)
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
