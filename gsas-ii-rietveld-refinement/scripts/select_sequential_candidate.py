#!/usr/bin/env python3
"""Compare completed sequential runs and select a conservative candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refinement_audit import sha256, write_json_atomic


def require_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"JSON file is missing or empty: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def verified_json(record: dict[str, Any], *, label: str) -> tuple[Path, dict[str, Any]]:
    path = Path(record["path"]).expanduser().resolve()
    declared_hash = record.get("sha256")
    if not declared_hash:
        raise ValueError(f"{label} has no declared SHA-256")
    actual_hash = sha256(path)
    if actual_hash != declared_hash:
        raise ValueError(
            f"{label} hash mismatch: declared {declared_hash}, "
            f"actual {actual_hash}"
        )
    return path, require_json(path)


def series_signature(manifest: dict[str, Any]) -> str:
    payload = []
    for frame in manifest.get("frames", []):
        pattern_hash = frame.get("pattern", {}).get("sha256")
        if not pattern_hash:
            raise ValueError(
                f"Frame {frame.get('frame_id')} has no staged pattern SHA-256"
            )
        payload.append(
            {
                "frame_id": frame["frame_id"],
                "order": int(frame["order"]),
                "pattern_sha256": pattern_hash,
            }
        )
    if not payload:
        raise ValueError("Sequence manifest has no frames")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def summarize_run(summary_path: Path) -> dict[str, Any]:
    summary = require_json(summary_path)
    audit_path, audit = verified_json(
        summary["audit_outputs"]["audit"], label="Sequential audit"
    )
    _, forward = verified_json(
        summary["audit_outputs"]["forward_json"],
        label="Forward sequence results",
    )
    _, reverse = verified_json(
        summary["audit_outputs"]["reverse_json"],
        label="Reverse sequence results",
    )
    _, validation = verified_json(
        summary["audit_outputs"]["report_validation"],
        label="Sequential report validation",
    )
    _, manifest = verified_json(
        summary["manifest"], label="Sequence manifest"
    )
    forward_ids = [frame["frame_id"] for frame in forward["frames"]]
    reverse_ids = [frame["frame_id"] for frame in reverse["frames"]]
    if forward_ids != reverse_ids:
        raise ValueError(
            f"Forward/reverse frame IDs differ for {summary_path}"
        )
    rwp_values = [
        float(frame["metrics"]["Rwp"]["value"])
        for result in (forward, reverse)
        for frame in result["frames"]
        if frame["metrics"]["Rwp"]["value"] is not None
    ]
    direction_deltas = [
        float(frame["Rwp_absolute_delta"])
        for frame in audit.get("frame_comparisons", [])
        if frame.get("Rwp_absolute_delta") is not None
    ]
    status = audit.get("status", "fail")
    validation_passed = validation.get("status") == "pass"
    return {
        "sample_id": summary.get("sample_id"),
        "summary": {
            "path": str(summary_path),
            "sha256": sha256(summary_path),
        },
        "audit": {"path": str(audit_path), "sha256": sha256(audit_path)},
        "status": status,
        "eligible": status != "fail" and validation_passed,
        "report_validation_status": validation.get("status"),
        "series_signature": series_signature(manifest),
        "frame_count": len(forward_ids),
        "hard_failure_count": len(audit.get("hard_failures", [])),
        "review_flag_count": len(audit.get("review_flags", [])),
        "median_Rwp": statistics.median(rwp_values) if rwp_values else None,
        "median_Rwp_direction_delta": (
            statistics.median(direction_deltas) if direction_deltas else None
        ),
        "settings": manifest.get("settings", {}),
    }


def candidate_rank(candidate: dict[str, Any]) -> tuple[Any, ...]:
    status_rank = {"pass": 0, "review": 1, "fail": 2}.get(
        candidate["status"], 3
    )
    direction_delta = candidate["median_Rwp_direction_delta"]
    tolerance = (
        candidate.get("settings", {})
        .get("audit_tolerances", {})
        .get("rwp_tolerance")
    )
    if tolerance is None:
        tolerance = 0.0
    tolerance = max(0.0, float(tolerance))
    if direction_delta is None:
        direction_exceeds_tolerance = 1
        direction_excess = float("inf")
    else:
        direction_delta = float(direction_delta)
        direction_exceeds_tolerance = int(direction_delta > tolerance)
        direction_excess = max(0.0, direction_delta - tolerance)
    return (
        status_rank,
        candidate["hard_failure_count"],
        candidate["review_flag_count"],
        direction_exceeds_tolerance,
        direction_excess,
        (
            candidate["median_Rwp"]
            if candidate["median_Rwp"] is not None
            else float("inf")
        ),
        direction_delta if direction_delta is not None else float("inf"),
        candidate["summary"]["sha256"],
    )


def select_candidates(paths: list[Path]) -> dict[str, Any]:
    if len(paths) < 2:
        raise ValueError("Supply at least two completed sequence run summaries")
    candidates = [summarize_run(path) for path in paths]
    sample_ids = {candidate["sample_id"] for candidate in candidates}
    if len(sample_ids) != 1:
        raise ValueError(
            "Sequential candidates must have the same sample_id"
        )
    signatures = {
        candidate["series_signature"] for candidate in candidates
    }
    if len(signatures) != 1:
        raise ValueError(
            "Sequential candidates must contain the same ordered pattern series"
        )
    candidates.sort(key=candidate_rank)
    selected = next(
        (candidate for candidate in candidates if candidate["eligible"]),
        None,
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "comparison_gate": (
            "Verified declared hashes and report validation; all candidates "
            "share one sample_id and the same ordered pattern hashes."
        ),
        "selection_policy": (
            "Prefer pass over review, reject fail, then minimize review flags "
            "and forward/reverse sensitivity above the predeclared tolerance. "
            "Direction deltas within tolerance are treated as scientifically "
            "equivalent before comparing median Rwp."
        ),
        "selected_summary_sha256": (
            selected["summary"]["sha256"] if selected else None
        ),
        "status": "selected" if selected else "no_eligible_candidate",
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-summary", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = select_candidates(
        [Path(value).expanduser().resolve() for value in args.run_summary]
    )
    output = Path(args.output).expanduser().resolve()
    write_json_atomic(output, result)
    print(output)
    if result["status"] != "selected":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
