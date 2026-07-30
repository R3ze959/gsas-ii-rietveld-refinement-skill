#!/usr/bin/env python3
"""Extract, compare, report, and validate GSAS-II sequential refinements."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refinement_audit import (
    _covariance_correlations,
    _residual_audit,
    format_value_esd,
    json_clean,
    resolve_gsasii_path,
    sha256,
    write_json_atomic,
)


CELL_LABELS = ("a", "b", "c", "alpha", "beta", "gamma", "volume")
REPORT_MARKER_START = "<!-- BEGIN GSAS-II-SEQUENTIAL-AUDIT schema=1 -->"
REPORT_MARKER_END = "<!-- END GSAS-II-SEQUENTIAL-AUDIT -->"


def _require_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise SystemExit(f"{label} is missing or empty: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must contain a JSON object: {path}")
    return value


def _cell_payload(seq: Any, phase: Any, histogram: str) -> dict[str, Any]:
    values, esds, unique = seq.get_cell_and_esd(phase, histogram)
    payload = {}
    for index, label in enumerate(CELL_LABELS):
        value = float(values[index])
        esd = float(esds[index]) if index < len(esds) and esds[index] else None
        payload[label] = {
            "value": value,
            "esd": esd,
            "formatted": format_value_esd(value, esd),
            "symmetry_independent": index in unique or index == 6,
        }
    return payload


def _mass_fractions(
    project: Any,
    sequence_data: dict[str, Any],
    histogram: Any,
) -> tuple[dict[str, Any], str | None]:
    """Compute sequential mass fractions from the per-frame covariance."""
    try:
        from GSASII import GSASIIstrMath  # type: ignore

        phases = {phase.name: phase.data for phase in project.phases()}
        histogram_id = int(histogram.data["data"][0]["hId"])
        values, esds = GSASIIstrMath.calcMassFracs(
            sequence_data["varyList"],
            sequence_data["covMatrix"],
            phases,
            histogram.name,
            histogram_id,
        )
        output: dict[str, Any] = {}
        for key, value in values.items():
            phase_id = int(str(key).split(":", 1)[0])
            phase_name = project.phase(phase_id).name
            esd = float(esds.get(key, 0.0)) or None
            output[phase_name] = {
                "value": float(value),
                "esd": esd,
                "formatted": format_value_esd(float(value), esd),
            }
        return output, None
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def _parameter(
    sequence_data: dict[str, Any], variable: str
) -> dict[str, Any] | None:
    parameters = sequence_data.get("parmDict", {})
    if variable not in parameters:
        return None
    value = parameters[variable]
    esd = None
    try:
        position = sequence_data["varyList"].index(variable)
        diagonal = float(sequence_data["covMatrix"][position, position])
        if diagonal >= 0:
            esd = math.sqrt(diagonal)
    except (ValueError, KeyError, IndexError, TypeError):
        pass
    numeric = float(value)
    return {
        "value": numeric,
        "esd": esd,
        "formatted": format_value_esd(numeric, esd),
        "refined": variable in sequence_data.get("varyList", []),
    }


def extract_sequence_results(
    gpx_path: Path,
    manifest: dict[str, Any],
    *,
    direction: str,
    gsasii_path: Path,
) -> dict[str, Any]:
    """Read one completed GPX and return frame-addressable sequential results."""
    gpx_path = gpx_path.expanduser().resolve()
    if not gpx_path.is_file() or gpx_path.stat().st_size <= 0:
        raise ValueError(f"Sequential GPX is missing or empty: {gpx_path}")
    if direction not in {"forward", "reverse", "forward_replay"}:
        raise ValueError(f"Unsupported sequential direction: {direction}")
    if str(gsasii_path) not in sys.path:
        sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIscriptable as G2sc  # type: ignore

    project = G2sc.G2Project(str(gpx_path))
    sequence = project.seqref()
    if sequence is None:
        raise ValueError(f"GPX has no Sequential results: {gpx_path}")
    sequence_histograms = set(sequence.histograms())
    controls = project["Controls"]["data"]
    frozen_by_histogram = controls.get("parmFrozen", {})
    frame_rows = []
    missing_frames = []
    phases = project.phases()
    ordered_frames = sorted(manifest["frames"], key=lambda item: item["order"])
    last_index = len(ordered_frames) - 1

    for ordinal, frame in enumerate(ordered_frames):
        histogram_name = frame["histogram"]
        if histogram_name not in sequence_histograms or histogram_name not in sequence.data:
            missing_frames.append(
                {
                    "frame_id": frame["frame_id"],
                    "histogram": histogram_name,
                    "reason": "No completed Sequential results entry",
                }
            )
            continue
        sequence_data = sequence.data[histogram_name]
        histogram = project.histogram(histogram_name)
        if histogram is None:
            missing_frames.append(
                {
                    "frame_id": frame["frame_id"],
                    "histogram": histogram_name,
                    "reason": "Histogram is missing from completed GPX",
                }
            )
            continue
        histogram_id = int(histogram.data["data"][0]["hId"])
        r_values = sequence_data.get("Rvals", {})
        correlations = _covariance_correlations(sequence_data)
        cells = {
            phase.name: _cell_payload(sequence, phase, histogram_name)
            for phase in phases
            if phase.data["Histograms"]
            .get(histogram_name, {})
            .get("Use", True)
        }
        mass_fractions, mass_fraction_error = _mass_fractions(
            project, sequence_data, histogram
        )
        phase_scales = {}
        for phase in phases:
            phase_id = int(phase.data["pId"])
            value = _parameter(
                sequence_data, f"{phase_id}:{histogram_id}:Scale"
            )
            if value is not None:
                phase_scales[phase.name] = value
        residuals = _residual_audit(histogram)
        run_index = ordinal if direction != "reverse" else last_index - ordinal
        frame_rows.append(
            {
                "frame_id": frame["frame_id"],
                "order": frame["order"],
                "run_index": run_index,
                "histogram": histogram_name,
                "pattern": frame["pattern"],
                "metadata": frame["metadata"],
                "phase_set": frame["phase_set"],
                "metrics": {
                    "Rwp": {
                        "value": (
                            float(r_values["Rwp"])
                            if r_values.get("Rwp") is not None
                            else None
                        ),
                        "source": "Sequential results.Rvals.Rwp",
                    },
                    "GOF": {
                        "value": (
                            float(r_values["GOF"])
                            if r_values.get("GOF") is not None
                            else None
                        ),
                        "source": "Sequential results.Rvals.GOF",
                    },
                },
                "convergence": {
                    "converged": bool(r_values.get("converged", False)),
                    "SVD0": int(r_values.get("SVD0", 0) or 0),
                    "max_shift_over_su": (
                        float(r_values["Max shft/sig"])
                        if r_values.get("Max shft/sig") is not None
                        else None
                    ),
                    "Nobs": r_values.get("Nobs"),
                    "Nvars": r_values.get("Nvars"),
                    "frozen_variables": [
                        str(item)
                        for item in frozen_by_histogram.get(histogram_name, [])
                    ],
                },
                "correlations": correlations,
                "cells": cells,
                "mass_fractions": mass_fractions,
                "mass_fraction_error": mass_fraction_error,
                "phase_scales": phase_scales,
                "sample_parameters": {
                    key: value
                    for key, value in {
                        "Scale": _parameter(
                            sequence_data, f":{histogram_id}:Scale"
                        ),
                        "DisplaceX": _parameter(
                            sequence_data, f":{histogram_id}:DisplaceX"
                        ),
                        "Zero": _parameter(
                            sequence_data, f":{histogram_id}:Zero"
                        ),
                    }.items()
                    if value is not None
                },
                "residual_audit": residuals,
                "vary_list": [str(item) for item in sequence_data.get("varyList", [])],
            }
        )

    frame_rows.sort(key=lambda item: item["order"])
    return json_clean(
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "gpx": {
                "path": str(gpx_path),
                "bytes": gpx_path.stat().st_size,
                "sha256": sha256(gpx_path),
            },
            "lst": {
                "path": str(gpx_path.with_suffix(".lst")),
                "exists": gpx_path.with_suffix(".lst").is_file(),
                "sha256": (
                    sha256(gpx_path.with_suffix(".lst"))
                    if gpx_path.with_suffix(".lst").is_file()
                    else None
                ),
            },
            "frame_count_expected": len(ordered_frames),
            "frame_count_completed": len(frame_rows),
            "missing_frames": missing_frames,
            "frames": frame_rows,
        }
    )


def write_results_csv(path: Path, results: dict[str, Any]) -> None:
    phase_names = sorted(
        {
            phase
            for frame in results["frames"]
            for phase in frame.get("cells", {})
        }
    )
    metadata_names = sorted(
        {
            key
            for frame in results["frames"]
            for key in frame.get("metadata", {})
        }
    )
    fieldnames = [
        "frame_id",
        "order",
        "run_index",
        "histogram",
        "Rwp",
        "GOF",
        "converged",
        "SVD0",
        "max_shift_over_su",
        "max_abs_correlation_percent",
        "frozen_variable_count",
    ]
    fieldnames.extend(f"metadata.{key}" for key in metadata_names)
    for phase in phase_names:
        fieldnames.extend(f"{phase}.cell.{key}" for key in CELL_LABELS)
        fieldnames.append(f"{phase}.mass_fraction")
    temporary = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for frame in results["frames"]:
            row: dict[str, Any] = {
                "frame_id": frame["frame_id"],
                "order": frame["order"],
                "run_index": frame["run_index"],
                "histogram": frame["histogram"],
                "Rwp": frame["metrics"]["Rwp"]["value"],
                "GOF": frame["metrics"]["GOF"]["value"],
                "converged": frame["convergence"]["converged"],
                "SVD0": frame["convergence"]["SVD0"],
                "max_shift_over_su": frame["convergence"][
                    "max_shift_over_su"
                ],
                "max_abs_correlation_percent": frame["correlations"][
                    "max_abs_percent"
                ],
                "frozen_variable_count": len(
                    frame["convergence"]["frozen_variables"]
                ),
            }
            for key in metadata_names:
                row[f"metadata.{key}"] = frame["metadata"].get(key)
            for phase in phase_names:
                phase_cell = frame.get("cells", {}).get(phase, {})
                for key in CELL_LABELS:
                    row[f"{phase}.cell.{key}"] = phase_cell.get(key, {}).get(
                        "value"
                    )
                row[f"{phase}.mass_fraction"] = (
                    frame.get("mass_fractions", {})
                    .get(phase, {})
                    .get("value")
                )
            writer.writerow(row)
    temporary.replace(path)


def _relative_delta(first: float, second: float) -> float:
    scale = max(abs(first), abs(second), 1e-15)
    return abs(first - second) / scale


def _continuity_outliers(results: dict[str, Any]) -> list[dict[str, Any]]:
    outliers = []
    for phase in sorted(
        {
            phase
            for frame in results["frames"]
            for phase in frame.get("cells", {})
        }
    ):
        series = [
            (
                frame["frame_id"],
                frame["order"],
                frame.get("cells", {})
                .get(phase, {})
                .get("volume", {})
                .get("value"),
            )
            for frame in results["frames"]
        ]
        series = [item for item in series if item[2] is not None]
        if len(series) < 4:
            continue
        differences = [
            float(series[index][2]) - float(series[index - 1][2])
            for index in range(1, len(series))
        ]
        median = statistics.median(differences)
        absolute_deviations = [abs(value - median) for value in differences]
        mad = statistics.median(absolute_deviations)
        if mad == 0:
            nonzero = [value for value in absolute_deviations if value > 0]
            mad = statistics.median(nonzero) if nonzero else 0.0
        threshold = max(6 * mad, abs(median) * 8, 1e-8)
        for index, difference in enumerate(differences, start=1):
            if abs(difference - median) > threshold:
                outliers.append(
                    {
                        "phase": phase,
                        "frame_id": series[index][0],
                        "order": series[index][1],
                        "volume_step": difference,
                        "median_volume_step": median,
                        "threshold": threshold,
                        "interpretation": (
                            "Review as a possible phase transition, bad frame, "
                            "or refinement instability; do not smooth it away."
                        ),
                    }
                )
    return outliers


def compare_directions(
    forward: dict[str, Any],
    reverse: dict[str, Any],
    *,
    cell_relative_tolerance: float = 5e-4,
    volume_relative_tolerance: float = 1e-3,
    mass_fraction_tolerance: float = 0.02,
    rwp_tolerance: float = 0.25,
) -> dict[str, Any]:
    """Compare path dependence and per-frame safety for two sequence runs."""
    forward_by_id = {frame["frame_id"]: frame for frame in forward["frames"]}
    reverse_by_id = {frame["frame_id"]: frame for frame in reverse["frames"]}
    frame_ids = sorted(
        set(forward_by_id) | set(reverse_by_id),
        key=lambda key: (
            forward_by_id.get(key) or reverse_by_id[key]
        )["order"],
    )
    comparisons = []
    hard_failures = []
    review_flags = []

    for frame_id in frame_ids:
        first = forward_by_id.get(frame_id)
        second = reverse_by_id.get(frame_id)
        if first is None or second is None:
            hard_failures.append(
                {
                    "frame_id": frame_id,
                    "reason": "Frame is missing from one direction",
                }
            )
            continue
        hard_issues = []
        review_issues = []
        for direction, frame in (("forward", first), ("reverse", second)):
            convergence = frame["convergence"]
            if not convergence["converged"]:
                hard_issues.append(f"{direction}: not converged")
            if convergence["SVD0"]:
                hard_issues.append(f"{direction}: SVD0={convergence['SVD0']}")
            if convergence["frozen_variables"]:
                hard_issues.append(
                    f"{direction}: frozen variables="
                    + ",".join(convergence["frozen_variables"])
                )
            maximum_shift = convergence.get("max_shift_over_su")
            if maximum_shift is not None and float(maximum_shift) > 1:
                review_issues.append(
                    f"{direction}: max shift/esd={maximum_shift:.6g}"
                )
            maximum = frame["correlations"].get("max_abs_percent")
            if maximum is not None and float(maximum) >= 95:
                review_issues.append(
                    f"{direction}: correlation={maximum:.3f}%"
                )
        rwp_first = first["metrics"]["Rwp"]["value"]
        rwp_second = second["metrics"]["Rwp"]["value"]
        rwp_delta = (
            abs(float(rwp_first) - float(rwp_second))
            if rwp_first is not None and rwp_second is not None
            else None
        )
        if rwp_delta is None:
            hard_issues.append("Rwp is missing from one direction")
        elif rwp_delta > rwp_tolerance:
            review_issues.append(f"Rwp direction delta={rwp_delta}")

        cell_deltas = {}
        for phase in sorted(set(first["cells"]) | set(second["cells"])):
            if phase not in first["cells"] or phase not in second["cells"]:
                hard_issues.append(f"phase {phase} missing in one direction")
                continue
            phase_deltas = {}
            for key in CELL_LABELS:
                value_first = float(first["cells"][phase][key]["value"])
                value_second = float(second["cells"][phase][key]["value"])
                relative = _relative_delta(value_first, value_second)
                tolerance = (
                    volume_relative_tolerance
                    if key == "volume"
                    else cell_relative_tolerance
                )
                phase_deltas[key] = {
                    "absolute": abs(value_first - value_second),
                    "relative": relative,
                    "tolerance": tolerance,
                    "pass": relative <= tolerance,
                }
                if relative > tolerance:
                    review_issues.append(
                        f"{phase} {key} direction relative delta={relative:.6g}"
                    )
            cell_deltas[phase] = phase_deltas

        fraction_deltas = {}
        for phase in sorted(
            set(first["mass_fractions"]) | set(second["mass_fractions"])
        ):
            if (
                phase not in first["mass_fractions"]
                or phase not in second["mass_fractions"]
            ):
                continue
            delta = abs(
                float(first["mass_fractions"][phase]["value"])
                - float(second["mass_fractions"][phase]["value"])
            )
            fraction_deltas[phase] = {
                "absolute": delta,
                "tolerance": mass_fraction_tolerance,
                "pass": delta <= mass_fraction_tolerance,
            }
            if delta > mass_fraction_tolerance:
                review_issues.append(
                    f"{phase} mass-fraction direction delta={delta:.6g}"
                )

        issues = hard_issues + review_issues
        comparison = {
            "frame_id": frame_id,
            "order": first["order"],
            "Rwp_absolute_delta": rwp_delta,
            "cell_deltas": cell_deltas,
            "mass_fraction_deltas": fraction_deltas,
            "issues": issues,
            "hard_issues": hard_issues,
            "review_issues": review_issues,
            "pass": not issues,
        }
        comparisons.append(comparison)
        if hard_issues:
            hard_failures.append(
                {"frame_id": frame_id, "reason": "; ".join(hard_issues)}
            )
        if review_issues:
            review_flags.append(
                {
                    "frame_id": frame_id,
                    "reason": "; ".join(review_issues),
                }
            )

    continuity = _continuity_outliers(forward)
    if continuity:
        review_flags.append(
            {
                "reason": (
                    f"{len(continuity)} robust cell-volume continuity "
                    "outlier(s)"
                )
            }
        )
    if forward["missing_frames"] or reverse["missing_frames"]:
        hard_failures.append(
            {
                "reason": "One or more expected frames lack completed results",
                "forward": forward["missing_frames"],
                "reverse": reverse["missing_frames"],
            }
        )
    status = "fail" if hard_failures else ("review" if review_flags else "pass")
    return json_clean(
        {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "thresholds": {
                "cell_relative": cell_relative_tolerance,
                "volume_relative": volume_relative_tolerance,
                "mass_fraction_absolute": mass_fraction_tolerance,
                "Rwp_absolute": rwp_tolerance,
                "maximum_allowed_correlation_percent": 95.0,
                "maximum_review_shift_over_su": 1.0,
            },
            "forward_gpx": forward["gpx"],
            "reverse_gpx": reverse["gpx"],
            "frame_comparisons": comparisons,
            "hard_failures": hard_failures,
            "review_flags": review_flags,
            "continuity_outliers": continuity,
        }
    )


def build_report(
    manifest: dict[str, Any],
    forward: dict[str, Any],
    reverse: dict[str, Any],
    audit: dict[str, Any],
) -> str:
    marker = {
        "schema_version": 1,
        "sample_id": manifest["sample_id"],
        "manifest_sha256": manifest["manifest"]["sha256"],
        "input_bundle_sha256": manifest.get("input_bundle", {}).get("sha256"),
        "forward_gpx_sha256": forward["gpx"]["sha256"],
        "reverse_gpx_sha256": reverse["gpx"]["sha256"],
        "frame_count": len(manifest["frames"]),
        "audit_status": audit["status"],
        "hard_failure_count": len(audit["hard_failures"]),
        "review_flag_count": len(audit["review_flags"]),
    }
    lines = [
        f"# GSAS-II sequential refinement report: {manifest['sample_id']}",
        "",
        "## Result status",
        "",
        f"- Sequential audit status: `{audit['status']}`",
        f"- Expected frames: {len(manifest['frames'])}",
        f"- Forward completed frames: {forward['frame_count_completed']}",
        f"- Reverse completed frames: {reverse['frame_count_completed']}",
        f"- Hard failures: {len(audit['hard_failures'])}",
        f"- Review flags: {len(audit['review_flags'])}",
        "- Real GSAS-II sequential refinement: yes",
        "- Figure generated by this skill: no",
        "",
        "## Input provenance",
        "",
        f"- Source manifest: `{manifest['manifest']['path']}`",
        f"- Instrument: `{manifest['instrument']['path']}`",
        (
            f"- Staged input-bundle manifest: "
            f"`{manifest.get('input_bundle', {}).get('path', 'not recorded')}`"
        ),
        "- Phase models:",
    ]
    lines.extend(
        f"  - {phase['name']}: `{phase['cif']['path']}`"
        for phase in manifest["phases"]
    )
    lines.extend(
        [
            "",
            "## Refinement design",
            "",
            "Representative anchor frames were refined before the sequence. "
            "The instrument profile was locked. Global phase cells were fixed "
            "for sequential fitting and histogram-dependent lattice changes "
            "were represented with HAP Dij/HStrain terms. Forward and reverse "
            "warm-start sequences were compared for path dependence.",
            "",
            "## Direction sensitivity",
            "",
            "| Frame | Order | Rwp delta | Issues |",
            "|---|---:|---:|---|",
        ]
    )
    for item in audit["frame_comparisons"]:
        issues = "; ".join(item["issues"]).replace("|", "/") or "none"
        delta = item["Rwp_absolute_delta"]
        lines.append(
            f"| {item['frame_id']} | {item['order']} | "
            f"{delta:.6g} | {issues} |"
            if delta is not None
            else f"| {item['frame_id']} | {item['order']} | n/a | {issues} |"
        )
    lines.extend(
        [
            "",
            "## Continuity review",
            "",
        ]
    )
    if audit["continuity_outliers"]:
        for item in audit["continuity_outliers"]:
            lines.append(
                f"- `{item['frame_id']}` / {item['phase']}: volume step "
                f"{item['volume_step']:.8g}. {item['interpretation']}"
            )
    else:
        lines.append("- No robust cell-volume continuity outlier was detected.")
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "- Sequential numerical convergence does not prove the phase model.",
            "- A discontinuity may be a real phase transition or a failed frame; "
            "it must be reviewed and must not be smoothed away.",
            "- Correlated profile, geometry, phase-fraction, or lattice terms "
            "must not be accepted only because Rwp decreases.",
            "- Operando laboratory XRD does not by itself prove dopant identity, "
            "site occupancy, vacancy concentration, or local structure.",
            "",
            "## Machine-verifiable audit",
            "",
            REPORT_MARKER_START,
            "```json",
            json.dumps(marker, ensure_ascii=False, indent=2),
            "```",
            REPORT_MARKER_END,
            "",
        ]
    )
    return "\n".join(lines)


def validate_report(
    report_path: Path,
    manifest: dict[str, Any],
    forward: dict[str, Any],
    reverse: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    text = report_path.read_text(encoding="utf-8")
    start = text.find(REPORT_MARKER_START)
    end = text.find(REPORT_MARKER_END)
    errors = []
    actual = None
    if start < 0 or end < 0 or end <= start:
        errors.append("Machine-verifiable sequential audit block is missing")
    else:
        block = text[start + len(REPORT_MARKER_START) : end]
        block = block.strip()
        if block.startswith("```json") and block.endswith("```"):
            block = block[len("```json") : -len("```")].strip()
        try:
            actual = json.loads(block)
        except Exception as exc:
            errors.append(f"Invalid audit JSON block: {exc}")
    expected = {
        "schema_version": 1,
        "sample_id": manifest["sample_id"],
        "manifest_sha256": manifest["manifest"]["sha256"],
        "input_bundle_sha256": manifest.get("input_bundle", {}).get("sha256"),
        "forward_gpx_sha256": forward["gpx"]["sha256"],
        "reverse_gpx_sha256": reverse["gpx"]["sha256"],
        "frame_count": len(manifest["frames"]),
        "audit_status": audit["status"],
        "hard_failure_count": len(audit["hard_failures"]),
        "review_flag_count": len(audit["review_flags"]),
    }
    if actual is not None and actual != expected:
        errors.append("Embedded audit block does not match refinement outputs")
    if "Figure generated by this skill: no" not in text:
        errors.append("Report does not state the no-figure boundary")
    return {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not errors else "fail",
        "report": {
            "path": str(report_path),
            "bytes": report_path.stat().st_size,
            "sha256": sha256(report_path),
        },
        "audit_status": audit["status"],
        "checks": {
            "embedded_audit_matches": actual == expected,
            "no_figure_boundary_present": (
                "Figure generated by this skill: no" in text
            ),
        },
        "errors": errors,
    }


def materialize_audit(
    *,
    manifest_path: Path,
    forward_gpx: Path,
    reverse_gpx: Path,
    output_dir: Path,
    gsasii_path: Path,
) -> dict[str, Path]:
    manifest = _require_json(manifest_path, "sequence manifest")
    output_dir.mkdir(parents=True, exist_ok=True)
    forward = extract_sequence_results(
        forward_gpx, manifest, direction="forward", gsasii_path=gsasii_path
    )
    reverse = extract_sequence_results(
        reverse_gpx, manifest, direction="reverse", gsasii_path=gsasii_path
    )
    forward_json = output_dir / "sequential_results_forward.json"
    reverse_json = output_dir / "sequential_results_reverse.json"
    forward_csv = output_dir / "sequential_results_forward.csv"
    reverse_csv = output_dir / "sequential_results_reverse.csv"
    audit_path = output_dir / "sequential_audit.json"
    report_path = output_dir / "sequential_report.md"
    validation_path = output_dir / "sequential_report_validation.json"
    write_json_atomic(forward_json, forward)
    write_json_atomic(reverse_json, reverse)
    write_results_csv(forward_csv, forward)
    write_results_csv(reverse_csv, reverse)
    audit = compare_directions(forward, reverse)
    audit["inputs"] = {
        "manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
        "forward_results": {
            "path": str(forward_json),
            "sha256": sha256(forward_json),
        },
        "reverse_results": {
            "path": str(reverse_json),
            "sha256": sha256(reverse_json),
        },
    }
    write_json_atomic(audit_path, audit)
    temporary_report = report_path.with_name(f".{report_path.name}.tmp")
    temporary_report.write_text(
        build_report(manifest, forward, reverse, audit), encoding="utf-8"
    )
    temporary_report.replace(report_path)
    validation = validate_report(
        report_path, manifest, forward, reverse, audit
    )
    write_json_atomic(validation_path, validation)
    if validation["status"] != "pass":
        raise RuntimeError(
            "Generated sequential report failed validation: "
            + "; ".join(validation["errors"])
        )
    return {
        "forward_json": forward_json,
        "reverse_json": reverse_json,
        "forward_csv": forward_csv,
        "reverse_csv": reverse_csv,
        "audit": audit_path,
        "report": report_path,
        "report_validation": validation_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-json", required=True)
    parser.add_argument("--forward-gpx", required=True)
    parser.add_argument("--reverse-gpx", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--gsasii-path")
    args = parser.parse_args()
    outputs = materialize_audit(
        manifest_path=Path(args.manifest_json).expanduser().resolve(),
        forward_gpx=Path(args.forward_gpx).expanduser().resolve(),
        reverse_gpx=Path(args.reverse_gpx).expanduser().resolve(),
        output_dir=Path(args.output_dir).expanduser().resolve(),
        gsasii_path=resolve_gsasii_path(args.gsasii_path),
    )
    for role, path in outputs.items():
        print(f"{role}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
