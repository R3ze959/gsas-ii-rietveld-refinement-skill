#!/usr/bin/env python3
"""Score an archived QPA prediction after reference values are unblinded."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from qpa_core import sha256_file, write_json_atomic


def parse_reference(values: list[str], *, units: str) -> dict[str, float]:
    output = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"reference must use NAME=value syntax: {value}")
        name, raw = (item.strip() for item in value.split("=", 1))
        numeric = float(raw)
        if units == "wt_percent":
            numeric /= 100.0
        if not name or name in output or not 0.0 <= numeric <= 1.0:
            raise ValueError(f"invalid or duplicate reference: {value}")
        output[name] = numeric
    if not output:
        raise ValueError("at least one --reference is required")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction", required=True)
    parser.add_argument("--reference", action="append", required=True)
    parser.add_argument("--reference-units", choices=("fraction", "wt_percent"), default="wt_percent")
    parser.add_argument("--reference-label", required=True)
    parser.add_argument("--reference-source", required=True)
    parser.add_argument("--max-absolute-error-wt-percent", type=float)
    parser.add_argument("--rmse-limit-wt-percent", type=float)
    parser.add_argument("--require-conservative-coverage", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    prediction_path = Path(args.prediction).expanduser().resolve()
    if not prediction_path.is_file():
        raise FileNotFoundError(f"prediction archive not found: {prediction_path}")
    output_path = Path(args.output).expanduser().resolve()
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    if (args.max_absolute_error_wt_percent is None) != (args.rmse_limit_wt_percent is None):
        parser.error("supply both accuracy limits or neither")
    reference = parse_reference(args.reference, units=args.reference_units)
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    fractions = prediction.get("mass_fractions")
    if not isinstance(fractions, dict):
        raise ValueError("prediction archive has no mass_fractions")
    if set(reference) != set(fractions):
        raise ValueError("reference and prediction phase names do not match")
    uncertainties = prediction.get("reported_uncertainties", {})
    rows = {}
    errors = []
    all_covered = True
    for name, truth in reference.items():
        value = float(fractions[name]["value"])
        statistical = fractions[name].get("esd")
        conservative = uncertainties.get(name, {}).get("conservative_combined")
        error = value - truth
        errors.append(error)
        statistical_coverage = (
            statistical is not None and abs(error) <= 2.0 * float(statistical)
        )
        conservative_coverage = (
            conservative is not None and abs(error) <= float(conservative)
        )
        all_covered = all_covered and bool(conservative_coverage)
        rows[name] = {
            "predicted_fraction": value,
            "reference_fraction": truth,
            "signed_error_wt_percent": 100.0 * error,
            "absolute_error_wt_percent": 100.0 * abs(error),
            "statistical_esd_wt_percent": (
                100.0 * float(statistical) if statistical is not None else None
            ),
            "covered_by_2sigma_statistical": statistical_coverage,
            "conservative_combined_uncertainty_wt_percent": (
                100.0 * float(conservative) if conservative is not None else None
            ),
            "covered_by_conservative_uncertainty": conservative_coverage,
        }
    rmse = 100.0 * math.sqrt(sum(error * error for error in errors) / len(errors))
    maximum = 100.0 * max(abs(error) for error in errors)
    thresholds_declared = args.max_absolute_error_wt_percent is not None
    failures = []
    if thresholds_declared:
        if maximum > float(args.max_absolute_error_wt_percent):
            failures.append("maximum_absolute_error_exceeds_limit")
        if rmse > float(args.rmse_limit_wt_percent):
            failures.append("rmse_exceeds_limit")
        if args.require_conservative_coverage and not all_covered:
            failures.append("conservative_uncertainty_does_not_cover_all_phases")
        status = "fail" if failures else "pass"
    else:
        status = "reported_not_graded"
    payload = {
        "schema_version": 1,
        "status": status,
        "prediction": {"path": str(prediction_path), "sha256": sha256_file(prediction_path)},
        "reference": {
            "label": args.reference_label,
            "source": args.reference_source,
            "values": reference,
        },
        "phase_results": rows,
        "metrics": {
            "RMSE_wt_percent": rmse,
            "maximum_absolute_error_wt_percent": maximum,
            "all_phases_covered_by_conservative_uncertainty": all_covered,
        },
        "acceptance": {
            "thresholds_supplied_to_scorer": thresholds_declared,
            "predeclared_before_prediction": "not inferable; verify against the frozen protocol manifest",
            "maximum_absolute_error_limit_wt_percent": args.max_absolute_error_wt_percent,
            "RMSE_limit_wt_percent": args.rmse_limit_wt_percent,
            "require_conservative_coverage": args.require_conservative_coverage,
            "failures": failures,
        },
    }
    write_json_atomic(output_path, payload)
    print(json.dumps(payload, indent=2))
    return 0 if status in {"pass", "reported_not_graded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
