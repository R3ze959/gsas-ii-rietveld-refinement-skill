#!/usr/bin/env python3
"""Issue a reproducible stage-validation decision from sequential audits."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Missing or empty JSON input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _series_metrics(results: dict[str, Any]) -> dict[str, Any]:
    frames = results.get("frames", [])
    rwps = [
        float(frame["metrics"]["Rwp"]["value"])
        for frame in frames
        if frame.get("metrics", {}).get("Rwp", {}).get("value") is not None
    ]
    correlations = [
        float(frame["correlations"]["max_abs_percent"])
        for frame in frames
        if frame.get("correlations", {}).get("max_abs_percent") is not None
    ]
    shifts = [
        float(frame["convergence"]["max_last_cycle_shift_over_su"])
        for frame in frames
        if frame.get("convergence", {}).get(
            "max_last_cycle_shift_over_su"
        )
        is not None
    ]
    return {
        "frame_count_expected": results.get("frame_count_expected"),
        "frame_count_completed": results.get("frame_count_completed"),
        "missing_frame_count": len(results.get("missing_frames", [])),
        "Rwp_percent": {
            "minimum": min(rwps) if rwps else None,
            "median": statistics.median(rwps) if rwps else None,
            "maximum": max(rwps) if rwps else None,
        },
        "maximum_absolute_correlation_percent": (
            max(correlations) if correlations else None
        ),
        "maximum_final_cycle_shift_over_su": max(shifts) if shifts else None,
    }


def validate(
    *,
    run_dir: Path,
    audit_dir: Path,
    reference_rwp_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    paths = {
        "manifest": run_dir / "sequence_manifest.json",
        "run_summary": run_dir / "sequence_run_summary.json",
        "anchor_summary": run_dir / "anchor_summary.json",
        "audit": audit_dir / "sequential_audit.json",
        "report_validation": audit_dir / "sequential_report_validation.json",
        "forward_results": audit_dir / "sequential_results_forward.json",
        "reverse_results": audit_dir / "sequential_results_reverse.json",
    }
    data = {key: read_json(path) for key, path in paths.items()}
    manifest = data["manifest"]
    audit = data["audit"]
    report_validation = data["report_validation"]
    forward_metrics = _series_metrics(data["forward_results"])
    reverse_metrics = _series_metrics(data["reverse_results"])
    audit_inputs = audit.get("inputs", {})

    checks = {
        "calibrated_instrument_declared": (
            manifest.get("instrument", {}).get("profile_status") == "calibrated"
        ),
        "figure_generation_disabled": (
            data["run_summary"].get("figure_generated") is False
        ),
        "forward_complete": (
            forward_metrics["frame_count_completed"]
            == forward_metrics["frame_count_expected"]
            and forward_metrics["missing_frame_count"] == 0
        ),
        "reverse_complete": (
            reverse_metrics["frame_count_completed"]
            == reverse_metrics["frame_count_expected"]
            and reverse_metrics["missing_frame_count"] == 0
        ),
        "audit_has_no_hard_failures": (
            audit.get("status") != "fail"
            and len(audit.get("hard_failures", [])) == 0
        ),
        "covariance_uncertainty_gaps_zero": (
            len(audit.get("uncertainty_gaps", [])) == 0
        ),
        "report_integrity_pass": report_validation.get("status") == "pass",
        "audit_bound_to_manifest": audit_inputs.get("manifest", {}).get(
            "sha256"
        )
        == sha256(paths["manifest"]),
        "audit_bound_to_forward_results": audit_inputs.get(
            "forward_results", {}
        ).get("sha256")
        == sha256(paths["forward_results"]),
        "audit_bound_to_reverse_results": audit_inputs.get(
            "reverse_results", {}
        ).get("sha256")
        == sha256(paths["reverse_results"]),
        "report_validation_bound_to_audit_status": (
            report_validation.get("audit_status") == audit.get("status")
        ),
    }
    required_checks_pass = all(checks.values())
    review_count = len(audit.get("review_flags", []))
    anchor_reviews = manifest.get("anchor_gate", {}).get(
        "correlation_reviews", []
    )
    if not required_checks_pass:
        status = "fail"
    elif review_count or anchor_reviews:
        status = "accepted_with_review"
    else:
        status = "pass"

    reference = None
    if reference_rwp_range is not None:
        low, high = reference_rwp_range
        all_rwps = []
        for direction in (forward_metrics, reverse_metrics):
            values = direction["Rwp_percent"]
            if values["minimum"] is not None:
                all_rwps.extend([values["minimum"], values["maximum"]])
        medians = [
            direction["Rwp_percent"]["median"]
            for direction in (forward_metrics, reverse_metrics)
        ]
        reference = {
            "range_percent": [low, high],
            "both_direction_medians_within_range": all(
                value is not None and low <= value <= high for value in medians
            ),
            "all_frame_extrema_within_range": bool(all_rwps)
            and min(all_rwps) >= low
            and max(all_rwps) <= high,
            "role": "contextual benchmark; not a substitute for audit gates",
        }

    return {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "scope": (
            "workflow/scientific validation only; accepted_with_review is not "
            "a publication-ready structural-model claim"
        ),
        "checks": checks,
        "audit_status": audit.get("status"),
        "review_flag_count": review_count,
        "anchor_correlation_reviews": anchor_reviews,
        "metrics": {
            "forward": forward_metrics,
            "reverse": reverse_metrics,
        },
        "reference_rwp": reference,
        "inputs": {
            key: {"path": str(path), "sha256": sha256(path)}
            for key, path in paths.items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--audit-dir")
    parser.add_argument("--output", required=True)
    parser.add_argument("--reference-rwp-min", type=float)
    parser.add_argument("--reference-rwp-max", type=float)
    args = parser.parse_args()
    if (args.reference_rwp_min is None) != (args.reference_rwp_max is None):
        raise SystemExit("Supply both reference Rwp bounds or neither")
    if (
        args.reference_rwp_min is not None
        and args.reference_rwp_min >= args.reference_rwp_max
    ):
        raise SystemExit("Reference Rwp minimum must be below maximum")
    run_dir = Path(args.run_dir).expanduser().resolve()
    audit_dir = (
        Path(args.audit_dir).expanduser().resolve()
        if args.audit_dir
        else run_dir / "results"
    )
    result = validate(
        run_dir=run_dir,
        audit_dir=audit_dir,
        reference_rwp_range=(
            (args.reference_rwp_min, args.reference_rwp_max)
            if args.reference_rwp_min is not None
            else None
        ),
    )
    output = Path(args.output).expanduser().resolve()
    write_json_atomic(output, result)
    print(f"status: {result['status']}")
    print(f"output: {output}")
    return 0 if result["status"] != "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
