#!/usr/bin/env python3
"""Run deterministic forward/reverse GSAS-II sequential powder refinements."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refinement_core import (
    CORE_MANIFEST_FIELDS,
    STANDARD_METADATA_FIELDS,
    classify_refinement_request,
    clean_name,
    require_file,
    require_ready_route,
)
from refinement_audit import (
    _covariance_correlations,
    default_data_root,
    format_value_esd,
    resolve_gsasii_path,
    sha256,
    write_json_atomic,
)
from sequential_audit import (
    CELL_LABELS,
    _last_cycle_shift_over_su,
    materialize_segmented_audit,
)


DEFAULT_STAGING_ROOT = default_data_root(
    "GSASII_REFINEMENT_STAGING", "GSAS-II_refinement_staging"
)


def copy_verified(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    source_hash = sha256(source)
    destination_hash = sha256(destination)
    if source_hash != destination_hash:
        raise RuntimeError(
            f"Hash mismatch while staging {source} as {destination}"
        )
    return {
        "source_path": str(source),
        "staged_path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": destination_hash,
    }


def optional_number(value: str, *, field: str, row_number: int) -> float | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError as exc:
        raise SystemExit(
            f"Manifest row {row_number} has nonnumeric {field}: {value!r}"
        ) from exc


def parse_phase_set(value: str, phase_names: list[str]) -> list[str]:
    if not value.strip():
        return list(phase_names)
    normalized = value.replace("|", ";").replace(",", ";")
    phases = [item.strip() for item in normalized.split(";") if item.strip()]
    unknown = sorted(set(phases) - set(phase_names))
    if unknown:
        raise SystemExit(
            "Manifest phase_set contains phase names not supplied by --phase-name: "
            + ", ".join(unknown)
        )
    if not phases:
        raise SystemExit("Manifest phase_set cannot be empty after parsing")
    return phases


def load_manifest(
    path: Path, phase_names: list[str], *, allow_missing_metadata: bool
) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise SystemExit(f"Sequence manifest has no header: {path}")
        fields = [field.strip() for field in reader.fieldnames]
        missing = {"frame_id", "pattern_path", "order"} - set(fields)
        if missing:
            raise SystemExit(
                "Sequence manifest is missing required columns: "
                + ", ".join(sorted(missing))
            )
        frames = []
        seen_ids = set()
        seen_orders = set()
        for row_number, source_row in enumerate(reader, start=2):
            row = {
                (key.strip() if key else ""): (value or "").strip()
                for key, value in source_row.items()
            }
            if not any(row.values()):
                continue
            frame_id = row["frame_id"]
            if not frame_id:
                raise SystemExit(f"Manifest row {row_number} has no frame_id")
            if frame_id in seen_ids:
                raise SystemExit(f"Duplicate frame_id in manifest: {frame_id}")
            try:
                order = int(row["order"])
            except ValueError as exc:
                raise SystemExit(
                    f"Manifest row {row_number} order must be an integer: "
                    f"{row['order']!r}"
                ) from exc
            if order in seen_orders:
                raise SystemExit(f"Duplicate order in manifest: {order}")
            pattern_value = row["pattern_path"]
            pattern = Path(pattern_value).expanduser()
            if not pattern.is_absolute():
                pattern = path.parent / pattern
            pattern = require_file(pattern, f"pattern for frame {frame_id}")
            metadata = {}
            for key in fields:
                if key in CORE_MANIFEST_FIELDS or key == "pattern_path":
                    continue
                value = row.get(key, "")
                if key in STANDARD_METADATA_FIELDS:
                    metadata[key] = optional_number(
                        value, field=key, row_number=row_number
                    )
                elif value:
                    metadata[key] = value
            frames.append(
                {
                    "frame_id": frame_id,
                    "order": order,
                    "pattern": {
                        "path": str(pattern),
                        "bytes": pattern.stat().st_size,
                        "sha256": sha256(pattern),
                    },
                    "metadata": metadata,
                    "phase_set": parse_phase_set(
                        row.get("phase_set", ""), phase_names
                    ),
                }
            )
            seen_ids.add(frame_id)
            seen_orders.add(order)
    if len(frames) < 2:
        raise SystemExit("Sequential refinement requires at least two frames")
    frames.sort(key=lambda item: item["order"])
    if not allow_missing_metadata:
        varying_metadata = []
        for key in STANDARD_METADATA_FIELDS:
            values = [
                frame["metadata"].get(key)
                for frame in frames
                if frame["metadata"].get(key) is not None
            ]
            if len(values) >= 2 and len(set(values)) >= 2:
                varying_metadata.append(key)
        if not varying_metadata:
            raise SystemExit(
                "Manifest needs at least one varying numeric metadata field "
                "(time, temperature, voltage, current, capacity, or state of "
                "charge). Use --allow-missing-metadata only for a deliberate "
                "file-order-only test."
            )
    return frames


def inspect_text_pattern(path: Path) -> dict[str, Any]:
    """Inspect an integrated text pattern without changing intensities."""
    two_theta: list[float] = []
    column_counts: list[int] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "!", ";")):
                continue
            tokens = stripped.replace(",", " ").split()
            if len(tokens) < 2:
                continue
            try:
                values = [float(token) for token in tokens[:3]]
            except ValueError:
                continue
            if not all(math.isfinite(value) for value in values):
                continue
            two_theta.append(values[0])
            column_counts.append(len(tokens))
    if len(two_theta) < 20:
        raise ValueError(
            f"Pattern has fewer than 20 parseable points: {path}"
        )
    steps = [
        two_theta[index] - two_theta[index - 1]
        for index in range(1, len(two_theta))
    ]
    if any(step <= 0 for step in steps):
        raise ValueError(f"Pattern 2theta values are not strictly increasing: {path}")
    median_step = statistics.median(steps)
    maximum_step_deviation = max(abs(step - median_step) for step in steps)
    return {
        "path": str(path),
        "point_count": len(two_theta),
        "two_theta_min": two_theta[0],
        "two_theta_max": two_theta[-1],
        "median_step": median_step,
        "maximum_relative_step_deviation": (
            maximum_step_deviation / median_step if median_step > 0 else None
        ),
        "minimum_column_count": min(column_counts),
        "maximum_column_count": max(column_counts),
        "contains_explicit_third_column": min(column_counts) >= 3,
    }


def preflight_patterns(
    frames: list[dict[str, Any]],
    *,
    mode: str,
    maximum_step_relative_delta: float = 0.02,
) -> dict[str, Any]:
    """Check point counts, ranges and step sizes before importing GSAS-II."""
    if mode == "off":
        return {"mode": mode, "status": "not_run", "patterns": [], "issues": []}
    patterns = []
    issues = []
    for frame in frames:
        try:
            inspection = inspect_text_pattern(Path(frame["pattern"]["path"]))
            inspection["frame_id"] = frame["frame_id"]
            inspection["order"] = frame["order"]
            patterns.append(inspection)
        except Exception as exc:
            issues.append(
                {
                    "frame_id": frame["frame_id"],
                    "order": frame["order"],
                    "reason": str(exc),
                }
            )
    if patterns:
        reference = patterns[0]
        for item in patterns[1:]:
            step_delta = abs(item["median_step"] - reference["median_step"]) / max(
                abs(reference["median_step"]), 1e-15
            )
            endpoint_tolerance = 2 * max(
                item["median_step"], reference["median_step"]
            )
            if step_delta > maximum_step_relative_delta:
                issues.append(
                    {
                        "frame_id": item["frame_id"],
                        "order": item["order"],
                        "reason": (
                            "median 2theta step differs from the first frame by "
                            f"{step_delta:.3%}"
                        ),
                    }
                )
            if (
                abs(item["two_theta_min"] - reference["two_theta_min"])
                > endpoint_tolerance
                or abs(item["two_theta_max"] - reference["two_theta_max"])
                > endpoint_tolerance
            ):
                issues.append(
                    {
                        "frame_id": item["frame_id"],
                        "order": item["order"],
                        "reason": "2theta range differs by more than two data steps",
                    }
                )
    status = "pass" if not issues else ("fail" if mode == "strict" else "review")
    result = {
        "mode": mode,
        "status": status,
        "maximum_step_relative_delta": maximum_step_relative_delta,
        "patterns": patterns,
        "issues": issues,
    }
    if status == "fail":
        raise SystemExit(
            "Pattern preflight failed: " + json.dumps(issues, ensure_ascii=False)
        )
    return result


def audit_manifest_metadata(
    frames: list[dict[str, Any]], *, file_order_only: bool
) -> dict[str, Any]:
    """Record whether experimental coordinates are complete and ordered."""
    fields: dict[str, Any] = {}
    varying = []
    for key in sorted(STANDARD_METADATA_FIELDS):
        values = [frame["metadata"].get(key) for frame in frames]
        finite = [
            float(value)
            for value in values
            if value is not None and math.isfinite(float(value))
        ]
        if not finite:
            continue
        differences = [
            finite[index] - finite[index - 1]
            for index in range(1, len(finite))
        ]
        if len(set(finite)) > 1:
            varying.append(key)
        fields[key] = {
            "present_count": len(finite),
            "missing_count": len(frames) - len(finite),
            "varies": len(set(finite)) > 1,
            "monotonic_non_decreasing": all(value >= 0 for value in differences),
            "monotonic_non_increasing": all(value <= 0 for value in differences),
        }
    complete_time = (
        "time_s" in fields
        and fields["time_s"]["missing_count"] == 0
        and fields["time_s"]["monotonic_non_decreasing"]
    )
    if complete_time:
        mode = "time_synchronized"
    elif varying:
        mode = "ordered_experimental_coordinates"
    else:
        mode = "file_order_only"
    return {
        "mode": mode,
        "file_order_only_explicitly_allowed": bool(file_order_only),
        "varying_fields": varying,
        "fields": fields,
        "scientific_status": (
            "exploratory" if mode == "file_order_only" else "ready"
        ),
    }


def parse_hstrain_masks(values: list[str], phase_count: int) -> list[str]:
    if not values:
        return ["all"] * phase_count
    if len(values) != phase_count:
        raise SystemExit(
            "Supply one --hstrain-mask per CIF/phase, or omit all masks"
        )
    for value in values:
        normalized = value.strip().lower()
        if normalized in {"all", "none"}:
            continue
        try:
            parsed = [int(item.strip()) for item in value.split(",")]
        except ValueError as exc:
            raise SystemExit(
                f"Invalid HStrain mask {value!r}; use all, none, or 1,0,..."
            ) from exc
        if not parsed or any(item not in {0, 1} for item in parsed):
            raise SystemExit(
                f"Invalid HStrain mask {value!r}; values must be 0 or 1"
            )
    return values


def parse_phase_options(
    values: list[str],
    phase_count: int,
    *,
    option: str,
    allowed: set[str],
    default: str,
) -> list[str]:
    if not values:
        return [default] * phase_count
    if len(values) != phase_count:
        raise SystemExit(
            f"Supply one {option} per CIF/phase, or omit all values"
        )
    normalized = [value.strip().lower() for value in values]
    invalid = sorted(set(normalized) - allowed)
    if invalid:
        raise SystemExit(
            f"Invalid {option} value(s): {', '.join(invalid)}; "
            f"choose from {', '.join(sorted(allowed))}"
        )
    return normalized


def parse_axes(values: list[str], phase_count: int) -> list[tuple[int, int, int]]:
    if not values:
        return [(0, 0, 1)] * phase_count
    if len(values) != phase_count:
        raise SystemExit(
            "Supply one --mustrain-axis per CIF/phase, or omit all axes"
        )
    axes = []
    for value in values:
        try:
            axis = tuple(int(item.strip()) for item in value.split(","))
        except ValueError as exc:
            raise SystemExit(
                f"Invalid --mustrain-axis {value!r}; use h,k,l"
            ) from exc
        if len(axis) != 3 or axis == (0, 0, 0):
            raise SystemExit(
                f"Invalid --mustrain-axis {value!r}; use three integers "
                "that are not all zero"
            )
        axes.append(axis)
    return axes


def parse_atom_flags(values: list[str], phase_count: int) -> list[str]:
    if not values:
        return [""] * phase_count
    if len(values) != phase_count:
        raise SystemExit(
            "Supply one --atom-flags per CIF/phase, or omit all flags"
        )
    output = []
    for value in values:
        normalized = value.strip().upper()
        if normalized in {"", "NONE", "OFF"}:
            normalized = ""
        if normalized not in {"", "X", "U", "XU", "UX"}:
            raise SystemExit(
                f"Invalid --atom-flags {value!r}; use none, X, U, or XU"
            )
        output.append("XU" if normalized == "UX" else normalized)
    return output


def resolve_mask(value: str, expected_length: int) -> bool | tuple[bool, ...]:
    normalized = value.strip().lower()
    if normalized == "all":
        return True
    if normalized == "none":
        return False
    items = tuple(bool(int(item.strip())) for item in value.split(","))
    if len(items) != expected_length:
        raise ValueError(
            f"HStrain mask {value!r} has {len(items)} values; "
            f"phase requires {expected_length}"
        )
    return items


def lock_instrument_profile(histogram: Any) -> None:
    instrument = histogram.data["Instrument Parameters"][0]
    for value in instrument.values():
        if isinstance(value, list) and len(value) > 2 and isinstance(value[2], bool):
            value[2] = False


def configure_histogram(
    histogram: Any,
    *,
    background_order: int,
    limits: tuple[float | None, float | None],
    displacement_mode: str,
    goniometer_radius: float | None,
    refine_displacement: bool,
) -> None:
    arrays = histogram.data["data"][1]
    actual_min = float(arrays[0][0])
    actual_max = float(arrays[0][-1])
    lower = limits[0] if limits[0] is not None else actual_min
    upper = limits[1] if limits[1] is not None else actual_max
    if lower >= upper:
        raise ValueError(
            f"Invalid limits for {histogram.name}: {lower} >= {upper}"
        )
    histogram.set_refinements({"Limits": [lower, upper]})
    sample = histogram.data["Sample Parameters"]
    supported_sample_parameters = [
        key
        for key in (
            "Scale",
            "Shift",
            "DisplaceX",
            "DisplaceY",
            "Transparency",
            "Absorption",
            "SurfRoughA",
            "SurfRoughB",
        )
        if key in sample
    ]
    histogram.clear_refinements(
        {
            "Sample Parameters": supported_sample_parameters,
            "Background": True,
        }
    )
    histogram.set_refinements(
        {
            "Background": {
                "type": "chebyschev-1",
                "no. coeffs": background_order,
                "refine": True,
            },
            "Sample Parameters": ["Scale"],
        }
    )
    if goniometer_radius is not None:
        sample["Gonio. radius"] = float(goniometer_radius)
    if displacement_mode == "displace-x" and refine_displacement:
        histogram.set_refinements({"Sample Parameters": ["DisplaceX"]})
    lock_instrument_profile(histogram)


def configure_phase(
    phase: Any,
    histogram: Any,
    *,
    use_phase: bool,
    refine_cell: bool,
    refine_fraction: bool,
    hstrain_mask: bool | tuple[bool, ...] = False,
) -> None:
    phase.set_refinements({"Atoms": {"all": ""}, "Cell": bool(refine_cell)})
    phase.clear_HAP_refinements(
        {
            "Scale": True,
            "Mustrain": True,
            "Size": True,
            "Pref.Ori.": True,
            "HStrain": True,
        },
        [histogram],
    )
    phase.set_HAP_refinements({"Use": use_phase}, [histogram])
    if not use_phase:
        return
    phase.set_HAP_refinements(
        {"Scale": bool(refine_fraction), "HStrain": hstrain_mask},
        [histogram],
    )


def configure_broadening(
    phase: Any,
    histograms: Any,
    *,
    size_model: str,
    mustrain_model: str,
    mustrain_axis: tuple[int, int, int],
) -> None:
    phase.clear_HAP_refinements(
        {"Size": True, "Mustrain": True}, histograms
    )
    if size_model != "off":
        phase.set_HAP_refinements(
            {"Size": {"type": size_model, "refine": True}},
            histograms,
        )
    if mustrain_model == "isotropic":
        phase.set_HAP_refinements(
            {"Mustrain": {"type": "isotropic", "refine": True}},
            histograms,
        )
    elif mustrain_model == "uniaxial":
        phase.set_HAP_refinements(
            {
                "Mustrain": {
                    "type": "uniaxial",
                    "direction": mustrain_axis,
                }
            },
            histograms,
        )
        if histograms == "all":
            histogram_names = list(phase.data["Histograms"])
        else:
            histogram_names = [
                item.name if hasattr(item, "name") else str(item)
                for item in histograms
            ]
        for histogram_name in histogram_names:
            flags = phase.data["Histograms"][histogram_name]["Mustrain"][2]
            if len(flags) < 2:
                raise ValueError(
                    f"Uniaxial Mustrain for {phase.name} has fewer than "
                    "two refinement flags"
                )
            flags[0] = True
            flags[1] = True


def add_phase_fraction_constraint(
    project: Any, histogram: Any, active_phases: list[Any]
) -> None:
    if len(active_phases) < 2:
        return
    histogram_id = int(histogram.data["data"][0]["hId"])
    variables = [
        f"{int(phase.data['pId'])}:{histogram_id}:Scale"
        for phase in active_phases
    ]
    initial = 1.0 / len(active_phases)
    for phase in active_phases:
        phase.HAPvalue("Scale", initial, [histogram])
    project.add_EqnConstr(1.0, variables, [1.0] * len(variables))


def run_refinement_passes(
    project: Any,
    *,
    maximum_passes: int,
    max_shift_over_su: float,
    max_rwp_over_rmin: float,
) -> int:
    passes = 0
    while passes < maximum_passes:
        project.refine()
        passes += 1
        covariance = project["Covariance"]["data"]
        r_values = covariance.get("Rvals", {})
        shift = r_values.get("Max shft/sig")
        histogram_summary = project.histograms()[0].data["data"][0]
        rwp = histogram_summary.get("wR")
        rwp_min = histogram_summary.get("wRmin")
        quality_ratio = (
            float(rwp) / float(rwp_min)
            if rwp is not None and rwp_min not in {None, 0}
            else None
        )
        if (
            r_values.get("converged", False)
            and shift is not None
            and float(shift) <= max_shift_over_su
            and quality_ratio is not None
            and quality_ratio <= max_rwp_over_rmin
        ):
            break
    return passes


def collect_anchor(project: Any, frame: dict[str, Any], passes: int) -> dict[str, Any]:
    covariance = project["Covariance"]["data"]
    r_values = covariance.get("Rvals", {})
    histogram_summary = project.histograms()[0].data["data"][0]
    rwp = histogram_summary.get("wR", r_values.get("Rwp"))
    rwp_min = histogram_summary.get("wRmin")
    cells = {}
    for phase in project.phases():
        values, esds = phase.get_cell_and_esd()
        cells[phase.name] = {
            key: {
                "value": float(values[
                    {
                        "a": "length_a",
                        "b": "length_b",
                        "c": "length_c",
                        "alpha": "angle_alpha",
                        "beta": "angle_beta",
                        "gamma": "angle_gamma",
                        "volume": "volume",
                    }[key]
                ]),
                "esd": (
                    float(esds.get(
                        {
                            "a": "length_a",
                            "b": "length_b",
                            "c": "length_c",
                            "alpha": "angle_alpha",
                            "beta": "angle_beta",
                            "gamma": "angle_gamma",
                            "volume": "volume",
                        }[key],
                        0.0,
                    ))
                    or None
                ),
            }
            for key in CELL_LABELS
        }
        for item in cells[phase.name].values():
            item["formatted"] = format_value_esd(item["value"], item["esd"])
    return {
        "frame_id": frame["frame_id"],
        "order": frame["order"],
        "histogram": project.histograms()[0].name,
        "refinement_passes": passes,
        "convergence": {
            "converged": bool(r_values.get("converged", False)),
            "SVD0": int(r_values.get("SVD0", 0) or 0),
            "max_shift_over_su": (
                float(r_values["Max shft/sig"])
                if r_values.get("Max shft/sig") is not None
                else None
            ),
        },
        "metrics": {
            "Rwp": rwp,
            "Rwp_min": rwp_min,
            "Rwp_over_Rwp_min": (
                float(rwp) / float(rwp_min)
                if rwp is not None and rwp_min not in {None, 0}
                else None
            ),
            "GOF": r_values.get("GOF"),
        },
        "correlations": _covariance_correlations(covariance),
        "cells": cells,
    }


def build_anchor(
    *,
    G2sc: Any,
    frame: dict[str, Any],
    output_path: Path,
    cifs: list[Path],
    phase_names: list[str],
    instrument: Path,
    xrd_format: str | None,
    cif_format: str,
    background_order: int,
    limits: tuple[float | None, float | None],
    displacement_mode: str,
    goniometer_radius: float | None,
    maximum_passes: int,
    max_shift_over_su: float,
    max_rwp_over_rmin: float,
    refine_phase_fractions: bool,
    size_models: list[str],
    mustrain_models: list[str],
    mustrain_axes: list[tuple[int, int, int]],
) -> dict[str, Any]:
    project = G2sc.G2Project(newgpx=str(output_path))
    histogram_kwargs = {"fmthint": xrd_format} if xrd_format else {}
    histogram = project.add_powder_histogram(
        frame["pattern"]["path"], str(instrument), **histogram_kwargs
    )
    phases = [
        project.add_phase(
            str(cif),
            phasename=name,
            histograms=[histogram],
            fmthint=cif_format,
        )
        for cif, name in zip(cifs, phase_names)
    ]
    configure_histogram(
        histogram,
        background_order=background_order,
        limits=limits,
        displacement_mode=displacement_mode,
        goniometer_radius=goniometer_radius,
        refine_displacement=False,
    )
    active = [
        phase
        for phase in phases
        if phase.name in frame["phase_set"]
    ]
    for phase in phases:
        configure_phase(
            phase,
            histogram,
            use_phase=phase in active,
            refine_cell=False,
            refine_fraction=(
                refine_phase_fractions and len(active) > 1 and phase in active
            ),
        )
    project.set_Controls("cycles", 8)
    project.save(str(output_path))
    if refine_phase_fractions and len(active) > 1:
        add_phase_fraction_constraint(project, histogram, active)
    passes = run_refinement_passes(
        project,
        maximum_passes=maximum_passes,
        max_shift_over_su=max_shift_over_su,
        max_rwp_over_rmin=max_rwp_over_rmin,
    )

    for phase in project.phases():
        phase.set_refinements(
            {"Cell": phase.name in frame["phase_set"]}
        )
    passes += run_refinement_passes(
        project,
        maximum_passes=maximum_passes,
        max_shift_over_su=max_shift_over_su,
        max_rwp_over_rmin=max_rwp_over_rmin,
    )

    if displacement_mode == "displace-x":
        project.histograms()[0].set_refinements(
            {"Sample Parameters": ["DisplaceX"]}
        )
        passes += run_refinement_passes(
            project,
            maximum_passes=maximum_passes,
            max_shift_over_su=max_shift_over_su,
            max_rwp_over_rmin=max_rwp_over_rmin,
        )
    project.save(str(output_path))
    stable_result = collect_anchor(project, frame, passes)
    stable_path = output_path.with_name(
        f".{output_path.stem}.stable{output_path.suffix}"
    )
    stable_lst = stable_path.with_suffix(".lst")
    shutil.copy2(output_path, stable_path)
    if output_path.with_suffix(".lst").is_file():
        shutil.copy2(output_path.with_suffix(".lst"), stable_lst)
    profile_attempt = None
    profile_attempt_exception = None
    if any(model != "off" for model in size_models + mustrain_models):
        try:
            for index, phase in enumerate(project.phases()):
                configure_broadening(
                    phase,
                    [histogram],
                    size_model=size_models[index],
                    mustrain_model=mustrain_models[index],
                    mustrain_axis=mustrain_axes[index],
                )
            passes += run_refinement_passes(
                project,
                maximum_passes=maximum_passes,
                max_shift_over_su=max_shift_over_su,
                max_rwp_over_rmin=max_rwp_over_rmin,
            )
            profile_attempt = collect_anchor(project, frame, passes)
        except Exception as exc:
            profile_attempt_exception = f"{type(exc).__name__}: {exc}"
        if (
            profile_attempt is None
            or not anchor_gate_passes(
                profile_attempt,
                max_shift_over_su=max_shift_over_su,
                max_rwp_over_rmin=max_rwp_over_rmin,
            )
        ):
            shutil.copy2(stable_path, output_path)
            if stable_lst.is_file():
                shutil.copy2(stable_lst, output_path.with_suffix(".lst"))
            project = G2sc.G2Project(str(output_path))
            passes = stable_result["refinement_passes"]
    project.save(str(output_path))
    result = collect_anchor(project, frame, passes)
    result["profile_seed_attempt"] = {
        "requested": any(
            model != "off" for model in size_models + mustrain_models
        ),
        "accepted": (
            profile_attempt is not None
            and anchor_gate_passes(
                profile_attempt,
                max_shift_over_su=max_shift_over_su,
                max_rwp_over_rmin=max_rwp_over_rmin,
            )
        ),
        "attempted_metrics": (
            profile_attempt["metrics"] if profile_attempt else None
        ),
        "attempted_convergence": (
            profile_attempt["convergence"] if profile_attempt else None
        ),
        "exception": profile_attempt_exception,
        "fallback_to_stable_anchor": (
            profile_attempt_exception is not None
            or (
                profile_attempt is not None
                and not anchor_gate_passes(
                    profile_attempt,
                    max_shift_over_su=max_shift_over_su,
                    max_rwp_over_rmin=max_rwp_over_rmin,
                )
            )
        ),
    }
    stable_path.unlink(missing_ok=True)
    stable_lst.unlink(missing_ok=True)
    stable_lst.unlink(missing_ok=True)
    return result


def select_anchor_frames(
    frames: list[dict[str, Any]], requested_orders: str | None
) -> list[dict[str, Any]]:
    by_order = {frame["order"]: frame for frame in frames}
    required_orders = {
        frames[0]["order"],
        frames[len(frames) // 2]["order"],
        frames[-1]["order"],
    }
    for index in range(1, len(frames)):
        if frames[index]["phase_set"] != frames[index - 1]["phase_set"]:
            required_orders.add(frames[index - 1]["order"])
            required_orders.add(frames[index]["order"])
    if requested_orders:
        orders = set(required_orders)
        for item in requested_orders.split(","):
            try:
                order = int(item.strip())
            except ValueError as exc:
                raise SystemExit(
                    f"Invalid --anchor-orders value: {item!r}"
                ) from exc
            if order not in by_order:
                raise SystemExit(f"Anchor order is absent from manifest: {order}")
            orders.add(order)
        return [by_order[order] for order in sorted(orders)]
    return [by_order[order] for order in sorted(required_orders)]


def anchor_gate_passes(
    result: dict[str, Any],
    *,
    max_shift_over_su: float,
    max_rwp_over_rmin: float,
) -> bool:
    convergence = result["convergence"]
    shift = convergence.get("max_shift_over_su")
    quality_ratio = result["metrics"].get("Rwp_over_Rwp_min")
    return bool(
        convergence.get("converged", False)
        and not convergence.get("SVD0", 0)
        and shift is not None
        and float(shift) <= max_shift_over_su
        and quality_ratio is not None
        and float(quality_ratio) <= max_rwp_over_rmin
    )


def build_anchor_segments(
    frames: list[dict[str, Any]],
    accepted_anchor_ids: set[str],
    *,
    direction: str,
) -> list[dict[str, Any]]:
    """Partition every frame into checkpoint-seeded directional segments."""
    if direction not in {"forward", "reverse"}:
        raise ValueError(f"Unsupported segment direction: {direction}")
    anchor_indices = [
        index
        for index, frame in enumerate(frames)
        if frame["frame_id"] in accepted_anchor_ids
    ]
    if not anchor_indices or anchor_indices[0] != 0 or anchor_indices[-1] != len(frames) - 1:
        raise ValueError("Accepted anchors must include the first and last frames")
    segments = []
    if direction == "forward":
        for segment_index, start in enumerate(anchor_indices[:-1]):
            stop = (
                anchor_indices[segment_index + 1]
                if segment_index + 2 < len(anchor_indices)
                else len(frames)
            )
            segment_frames = frames[start:stop]
            if not segment_frames:
                continue
            segments.append(
                {
                    "segment_id": f"forward_{segment_index:03d}",
                    "checkpoint_frame_id": frames[start]["frame_id"],
                    "frames": segment_frames,
                }
            )
    else:
        descending = list(reversed(anchor_indices))
        for segment_index, high in enumerate(descending[:-1]):
            low = (
                descending[segment_index + 1]
            )
            lower_bound = (
                low + 1 if segment_index + 2 < len(descending) else 0
            )
            segment_frames = list(reversed(frames[lower_bound : high + 1]))
            if not segment_frames:
                continue
            segments.append(
                {
                    "segment_id": f"reverse_{segment_index:03d}",
                    "checkpoint_frame_id": frames[high]["frame_id"],
                    "frames": segment_frames,
                }
            )
    covered = [frame["frame_id"] for segment in segments for frame in segment["frames"]]
    expected = [frame["frame_id"] for frame in frames]
    if sorted(covered) != sorted(expected) or len(covered) != len(set(covered)):
        raise RuntimeError(
            f"{direction} checkpoint segmentation did not cover each frame exactly once"
        )
    return segments


def read_reference_cells(
    G2sc: Any,
    anchor_path: Path,
    phase_names: list[str],
) -> dict[str, list[Any]]:
    """Read one common global cell for direction-comparable sequences."""
    project = G2sc.G2Project(str(anchor_path))
    by_name = {phase.name: phase for phase in project.phases()}
    missing = sorted(set(phase_names) - set(by_name))
    if missing:
        raise ValueError(
            "Reference anchor is missing phase(s): " + ", ".join(missing)
        )
    return {
        name: list(by_name[name].data["General"]["Cell"])
        for name in phase_names
    }


def hstrain_offsets_for_cells(
    *,
    reference_cell: list[Any],
    target_cell: list[Any],
    strain_names: list[str],
    cell_to_reciprocal: Any,
) -> list[float]:
    """Convert a target anchor cell into HStrain offsets from one reference."""
    labels = ("D11", "D22", "D33", "D12", "D13", "D23")
    reference_a = cell_to_reciprocal(reference_cell[1:7])
    target_a = cell_to_reciprocal(target_cell[1:7])
    differences = {
        label: float(target - reference)
        for label, target, reference in zip(labels, target_a, reference_a)
    }
    return [
        differences.get(name, 0.0)
        for name in strain_names
    ]


def prepare_sequence_base(
    *,
    G2sc: Any,
    seed_anchor_path: Path,
    base_path: Path,
    frames: list[dict[str, Any]],
    phase_names: list[str],
    instrument: Path,
    xrd_format: str | None,
    background_order: int,
    limits: tuple[float | None, float | None],
    displacement_mode: str,
    goniometer_radius: float | None,
    refine_displacement_in_sequence: bool,
    refine_phase_fractions: bool,
    hstrain_masks: list[str],
    max_cycles: int,
    reference_cells: dict[str, list[Any]],
) -> tuple[Any, list[str], dict[str, Any]]:
    from GSASII import GSASIIlattice as G2lat  # type: ignore
    from GSASII import GSASIIspc as G2spc  # type: ignore

    shutil.copy2(seed_anchor_path, base_path)
    project = G2sc.G2Project(str(base_path))
    first_histogram = project.histograms()[0]
    first_frame = frames[0]
    first_frame["histogram"] = first_histogram.name
    for frame in frames[1:]:
        kwargs = {"fmthint": xrd_format} if xrd_format else {}
        histogram = project.add_powder_histogram(
            frame["pattern"]["path"],
            str(instrument),
            phases="all",
            **kwargs,
        )
        frame["histogram"] = histogram.name

    project.copyHistParms(0, "all", ["b", "i", "l"])
    seed_hstrain_offsets = {}
    for phase in project.phases():
        if phase.name not in reference_cells:
            raise ValueError(
                f"No common reference cell supplied for phase {phase.name}"
            )
        target_cell = list(phase.data["General"]["Cell"])
        strain_names = list(
            G2spc.HStrainNames(phase.data["General"]["SGData"])
        )
        seed_hstrain_offsets[phase.name] = {
            "strain_names": strain_names,
            "values": hstrain_offsets_for_cells(
                reference_cell=reference_cells[phase.name],
                target_cell=target_cell,
                strain_names=strain_names,
                cell_to_reciprocal=G2lat.cell2A,
            ),
            "target_anchor_cell": target_cell,
        }
        phase.data["General"]["Cell"] = list(reference_cells[phase.name])
        phase.copyHAPvalues(0, "all")
        phase.set_refinements({"Cell": False, "Atoms": {"all": ""}})

    seed_sample = first_histogram.data["Sample Parameters"]
    constant_phase_set = all(
        frame["phase_set"] == first_frame["phase_set"] for frame in frames
    )
    if refine_phase_fractions and not constant_phase_set:
        raise ValueError(
            "Sequential phase-fraction refinement currently requires the same "
            "declared phase_set in every frame. Use fixed fractions or split "
            "the sequence at the phase transition."
        )

    for frame in frames:
        histogram = project.histogram(frame["histogram"])
        configure_histogram(
            histogram,
            background_order=background_order,
            limits=limits,
            displacement_mode=displacement_mode,
            goniometer_radius=goniometer_radius,
            refine_displacement=refine_displacement_in_sequence,
        )
        if displacement_mode == "displace-x":
            histogram.data["Sample Parameters"]["DisplaceX"][0] = float(
                seed_sample["DisplaceX"][0]
            )
        for index, phase in enumerate(project.phases()):
            use_phase = phase.name in frame["phase_set"]
            expected = len(
                phase.data["Histograms"][histogram.name]["HStrain"][1]
            )
            mask = resolve_mask(hstrain_masks[index], expected)
            configure_phase(
                phase,
                histogram,
                use_phase=use_phase,
                refine_cell=False,
                refine_fraction=(
                    refine_phase_fractions
                    and len(frame["phase_set"]) > 1
                    and use_phase
                ),
                hstrain_mask=mask if use_phase else False,
            )
            enabled = (
                [mask] * expected if isinstance(mask, bool) else list(mask)
            )
            offsets = seed_hstrain_offsets[phase.name]["values"]
            phase.data["Histograms"][histogram.name]["HStrain"][0] = [
                float(value) if flag and use_phase else 0.0
                for value, flag in zip(offsets, enabled)
            ]

    if "Constraints" in project.data:
        project.data["Constraints"]["data"]["_seqmode"] = "auto-wildcard"
    project.set_Controls("cycles", max_cycles)
    project.set_Controls("sequential", project.histograms())
    project.set_Controls("seqCopy", True)
    project.set_Controls("Reverse Seq", False)
    project.save(str(base_path))
    return (
        project,
        [histogram.name for histogram in project.histograms()],
        seed_hstrain_offsets,
    )


def run_sequence(
    *,
    G2sc: Any,
    base_path: Path,
    output_path: Path,
    reverse: bool,
    max_cycles: int,
    stage_maximum_passes: int,
    phase_names: list[str],
    refine_displacement: bool,
    refine_phase_fractions: bool,
    size_models: list[str],
    mustrain_models: list[str],
    mustrain_axes: list[tuple[int, int, int]],
    atom_flags: list[str],
) -> dict[str, Any]:
    shutil.copy2(base_path, output_path)
    project = G2sc.G2Project(str(output_path))
    expected = [histogram.name for histogram in project.histograms()]
    project.set_Controls("sequential", project.histograms())
    project.set_Controls("cycles", max_cycles)
    project.set_Controls("seqCopy", True)
    # Each direction is built from its matching anchor in the intended run
    # order. Keep GSAS-II's native reverse switch off so the first histogram
    # is always the matching seed anchor.
    project.set_Controls("Reverse Seq", False)
    stages = []
    halted_after_stage = None

    def execute_stage(
        name: str, required_tokens: tuple[str, ...] = ()
    ) -> bool:
        exception_message = None
        completed: list[str] = []
        stage_failures: list[str] = []
        pass_diagnostics = []
        for pass_index in range(1, stage_maximum_passes + 1):
            try:
                project.refine()
            except Exception as exc:
                exception_message = f"{type(exc).__name__}: {exc}"
            finally:
                project.save(str(output_path))
            sequence = project.seqref()
            completed = sequence.histograms() if sequence is not None else []
            current_failures = []
            final_shifts = []
            if exception_message:
                current_failures.append(
                    f"GSAS-II exception: {exception_message}"
                )
            if sequence is not None:
                for histogram_name in completed:
                    data = sequence.data[histogram_name]
                    r_values = data.get("Rvals", {})
                    if not r_values.get("converged", False):
                        current_failures.append(
                            f"{histogram_name}: not converged"
                        )
                    if int(r_values.get("SVD0", 0) or 0):
                        current_failures.append(
                            f"{histogram_name}: SVD0={r_values['SVD0']}"
                        )
                    last_shift = _last_cycle_shift_over_su(data)
                    if last_shift is not None:
                        final_shifts.append(last_shift)
                    vary_list = [
                        str(item) for item in data.get("varyList", [])
                    ]
                    for token in required_tokens:
                        if not any(
                            token in variable for variable in vary_list
                        ):
                            current_failures.append(
                                f"{histogram_name}: required variable family "
                                f"{token!r} is absent"
                            )
            if set(expected) != set(completed):
                current_failures.append(
                    f"completed {len(completed)} of {len(expected)} histograms"
                )
            maximum_final_shift = (
                max(final_shifts) if final_shifts else None
            )
            pass_diagnostics.append(
                {
                    "pass": pass_index,
                    "completed_histograms": len(completed),
                    "maximum_final_cycle_shift_over_su": maximum_final_shift,
                    "failures": current_failures,
                }
            )
            stage_failures = current_failures
            if exception_message:
                break
            if (
                not current_failures
                and maximum_final_shift is not None
                and maximum_final_shift <= 1.0
            ):
                break
        stage_path = output_path.with_name(
            f"{output_path.stem}_{name}{output_path.suffix}"
        )
        shutil.copy2(output_path, stage_path)
        stages.append(
            {
                "name": name,
                "path": str(stage_path),
                "sha256": sha256(stage_path),
                "completed_histograms": completed,
                "complete": set(expected) == set(completed),
                "failures": stage_failures,
                "exception": exception_message,
                "refinement_passes": len(pass_diagnostics),
                "stability_target": {
                    "maximum_final_cycle_shift_over_su": 1.0,
                    "met": bool(
                        pass_diagnostics
                        and pass_diagnostics[-1][
                            "maximum_final_cycle_shift_over_su"
                        ]
                        is not None
                        and pass_diagnostics[-1][
                            "maximum_final_cycle_shift_over_su"
                        ]
                        <= 1.0
                    ),
                },
                "pass_diagnostics": pass_diagnostics,
            }
        )
        return not stage_failures

    # Stage 1: only the stable terms configured in the base project
    # (background, histogram scale and HAP Dij/HStrain) are active.
    stage_ok = execute_stage("stage1_stable")
    if not stage_ok:
        halted_after_stage = "stage1_stable"

    if stage_ok and refine_displacement:
        for histogram in project.histograms():
            if "DisplaceX" in histogram.data["Sample Parameters"]:
                histogram.set_refinements(
                    {"Sample Parameters": ["DisplaceX"]}
                )
        project.set_Controls("seqCopy", False)
        stage_ok = execute_stage("stage2_geometry", ("DisplaceX",))
        if not stage_ok:
            halted_after_stage = "stage2_geometry"

    complex_stage = refine_phase_fractions or any(
        model != "off" for model in size_models + mustrain_models
    )
    if stage_ok and complex_stage:
        for index, phase in enumerate(project.phases()):
            phase.set_HAP_refinements(
                {"Scale": bool(refine_phase_fractions)}, "all"
            )
            configure_broadening(
                phase,
                "all",
                size_model=size_models[index],
                mustrain_model=mustrain_models[index],
                mustrain_axis=mustrain_axes[index],
            )
        project.set_Controls("seqCopy", False)
        required = []
        if refine_phase_fractions:
            required.append(":Scale")
        if any(model != "off" for model in size_models):
            required.append("Size;")
        if any(model != "off" for model in mustrain_models):
            required.append("Mustrain;")
        stage_ok = execute_stage("stage3_phase_profile", tuple(required))
        if not stage_ok:
            halted_after_stage = "stage3_phase_profile"

    if stage_ok and any(atom_flags):
        atomic_gate_failures = []
        sequence = project.seqref()
        if sequence is None:
            atomic_gate_failures.append(
                "No completed pre-atomic sequential covariance is available"
            )
        else:
            for histogram_name in sequence.histograms():
                data = sequence.data[histogram_name]
                r_values = data.get("Rvals", {})
                observations = r_values.get("Nobs")
                variables = r_values.get("Nvars")
                if (
                    observations is None
                    or variables in {None, 0}
                    or float(observations) / float(variables) < 10
                ):
                    atomic_gate_failures.append(
                        f"{histogram_name}: Nobs/Nvars is unavailable or below 10"
                    )
                maximum = _covariance_correlations(data).get(
                    "max_abs_percent"
                )
                if maximum is None or float(maximum) >= 95:
                    atomic_gate_failures.append(
                        f"{histogram_name}: pre-atomic maximum correlation "
                        f"is {maximum}%"
                    )
        if atomic_gate_failures:
            stages.append(
                {
                    "name": "stage4_atoms_gate",
                    "path": None,
                    "sha256": None,
                    "completed_histograms": (
                        sequence.histograms() if sequence is not None else []
                    ),
                    "complete": False,
                    "failures": atomic_gate_failures,
                    "exception": None,
                }
            )
            stage_ok = False
            halted_after_stage = "stage4_atoms_gate"

    if stage_ok and any(atom_flags):
        by_name = {phase.name: phase for phase in project.phases()}
        for phase_name, flags in zip(phase_names, atom_flags):
            by_name[phase_name].set_refinements(
                {"Atoms": {"all": flags}}
            )
        project.set_Controls("seqCopy", False)
        required = []
        if any("X" in flags for flags in atom_flags):
            required.append("::dA")
        if any("U" in flags for flags in atom_flags):
            required.append("AUiso")
        stage_ok = execute_stage("stage4_atoms", tuple(required))
        if not stage_ok:
            halted_after_stage = "stage4_atoms"

    project.save(str(output_path))
    sequence = project.seqref()
    completed = sequence.histograms() if sequence is not None else []
    return {
        "path": str(output_path),
        "bytes": output_path.stat().st_size,
        "sha256": sha256(output_path),
        "direction": "reverse" if reverse else "forward",
        "expected_histograms": expected,
        "completed_histograms": completed,
        "complete": set(expected) == set(completed),
        "stages": stages,
        "halted_after_stage": halted_after_stage,
        "integrity_status": "pass" if stage_ok else "partial",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--cif", action="append", required=True)
    parser.add_argument("--phase-name", action="append", default=[])
    parser.add_argument("--instrument", required=True)
    parser.add_argument(
        "--instrument-profile-status",
        choices=("calibrated", "uncalibrated"),
        required=True,
    )
    parser.add_argument("--gsasii-path")
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    parser.add_argument("--run-id")
    parser.add_argument("--xrd-format")
    parser.add_argument("--cif-format", default="CIF")
    parser.add_argument("--background-order", type=int, default=6)
    parser.add_argument("--two-theta-min", type=float)
    parser.add_argument("--two-theta-max", type=float)
    parser.add_argument(
        "--displacement-mode",
        choices=("none", "displace-x"),
        default="none",
    )
    parser.add_argument("--goniometer-radius", type=float)
    parser.add_argument(
        "--refine-displacement-in-sequence", action="store_true"
    )
    parser.add_argument(
        "--phase-fractions",
        choices=("fixed", "refine"),
        default="fixed",
    )
    parser.add_argument(
        "--hstrain-mask",
        action="append",
        default=[],
        help="One per phase: all, none, or a symmetry-sized 1,0,... mask",
    )
    parser.add_argument(
        "--size-model",
        action="append",
        default=[],
        help="One per phase: off, isotropic, or uniaxial",
    )
    parser.add_argument(
        "--mustrain-model",
        action="append",
        default=[],
        help="One per phase: off, isotropic, or uniaxial",
    )
    parser.add_argument(
        "--mustrain-axis",
        action="append",
        default=[],
        help="One h,k,l direction per phase; used for uniaxial microstrain",
    )
    parser.add_argument(
        "--atom-flags",
        action="append",
        default=[],
        help="One per phase: none, X, U, or XU; applied only in final stage",
    )
    parser.add_argument("--anchor-orders")
    parser.add_argument("--anchor-max-passes", type=int, default=20)
    parser.add_argument("--max-shift-over-su", type=float, default=0.5)
    parser.add_argument("--max-anchor-rwp-over-rmin", type=float, default=3.0)
    parser.add_argument("--max-cycles", type=int, default=10)
    parser.add_argument(
        "--sequential-stage-max-passes",
        type=int,
        default=3,
        help=(
            "Repeat each sequential stage up to this many times while the "
            "maximum final-cycle shift/esd remains above 1"
        ),
    )
    parser.add_argument(
        "--pattern-preflight",
        choices=("strict", "warn", "off"),
        default="strict",
    )
    parser.add_argument(
        "--intensity-weighting",
        choices=("preserve-input", "gsas-importer", "unknown"),
        default="preserve-input",
        help="Provenance declaration only; the driver never rewrites weights",
    )
    parser.add_argument("--cell-relative-tolerance", type=float, default=5e-4)
    parser.add_argument("--volume-relative-tolerance", type=float, default=1e-3)
    parser.add_argument("--mass-fraction-tolerance", type=float, default=0.02)
    parser.add_argument("--rwp-direction-tolerance", type=float, default=0.25)
    parser.add_argument("--residual-minimum-sigma", type=float, default=6.0)
    parser.add_argument(
        "--residual-minimum-pattern-percent", type=float, default=2.0
    )
    parser.add_argument(
        "--residual-hard-pattern-percent", type=float, default=10.0
    )
    parser.add_argument(
        "--residual-two-theta-tolerance", type=float, default=0.12
    )
    parser.add_argument("--allow-atomic-refinement", action="store_true")
    parser.add_argument("--atomic-justification")
    parser.add_argument("--allow-missing-metadata", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    request_classification = classify_refinement_request(
        manifest=args.manifest,
        intent="refine",
        declared_mode="sequential",
        allow_file_order_only=args.allow_missing_metadata,
    )
    require_ready_route(
        request_classification,
        "sequential_refinement",
    )

    if not 2 <= args.background_order <= 20:
        raise SystemExit("--background-order must be between 2 and 20")
    if not 1 <= args.anchor_max_passes <= 20:
        raise SystemExit("--anchor-max-passes must be between 1 and 20")
    if not 1 <= args.max_cycles <= 50:
        raise SystemExit("--max-cycles must be between 1 and 50")
    if not 1 <= args.sequential_stage_max_passes <= 10:
        raise SystemExit(
            "--sequential-stage-max-passes must be between 1 and 10"
        )
    if args.max_shift_over_su <= 0:
        raise SystemExit("--max-shift-over-su must be positive")
    if args.max_anchor_rwp_over_rmin <= 1:
        raise SystemExit("--max-anchor-rwp-over-rmin must be greater than 1")
    positive_tolerances = {
        "--cell-relative-tolerance": args.cell_relative_tolerance,
        "--volume-relative-tolerance": args.volume_relative_tolerance,
        "--mass-fraction-tolerance": args.mass_fraction_tolerance,
        "--rwp-direction-tolerance": args.rwp_direction_tolerance,
        "--residual-minimum-sigma": args.residual_minimum_sigma,
        "--residual-minimum-pattern-percent": (
            args.residual_minimum_pattern_percent
        ),
        "--residual-hard-pattern-percent": args.residual_hard_pattern_percent,
        "--residual-two-theta-tolerance": (
            args.residual_two_theta_tolerance
        ),
    }
    invalid_tolerances = [
        name for name, value in positive_tolerances.items() if value <= 0
    ]
    if invalid_tolerances:
        raise SystemExit(
            "Audit tolerances must be positive: "
            + ", ".join(invalid_tolerances)
        )
    if (
        args.residual_hard_pattern_percent
        < args.residual_minimum_pattern_percent
    ):
        raise SystemExit(
            "--residual-hard-pattern-percent must be at least "
            "--residual-minimum-pattern-percent"
        )
    if args.displacement_mode == "displace-x":
        if args.goniometer_radius is None or args.goniometer_radius <= 0:
            raise SystemExit(
                "--displace-x requires a positive --goniometer-radius"
            )
    elif args.refine_displacement_in_sequence:
        raise SystemExit(
            "--refine-displacement-in-sequence requires "
            "--displacement-mode displace-x"
        )
    if (
        args.two_theta_min is not None
        and args.two_theta_max is not None
        and args.two_theta_min >= args.two_theta_max
    ):
        raise SystemExit("two-theta minimum must be less than maximum")

    manifest_path = require_file(args.manifest, "sequence manifest")
    cifs = [require_file(value, "phase CIF") for value in args.cif]
    instrument = require_file(args.instrument, "instrument parameter file")
    if args.phase_name and len(args.phase_name) != len(cifs):
        raise SystemExit("Supply one --phase-name per --cif, or omit all names")
    phase_names = (
        [clean_name(value) for value in args.phase_name]
        if args.phase_name
        else [clean_name(path.stem) for path in cifs]
    )
    if len(set(phase_names)) != len(phase_names):
        raise SystemExit("Phase names must be unique")
    hstrain_masks = parse_hstrain_masks(args.hstrain_mask, len(cifs))
    size_models = parse_phase_options(
        args.size_model,
        len(cifs),
        option="--size-model",
        allowed={"off", "isotropic", "uniaxial"},
        default="off",
    )
    mustrain_models = parse_phase_options(
        args.mustrain_model,
        len(cifs),
        option="--mustrain-model",
        allowed={"off", "isotropic", "uniaxial"},
        default="off",
    )
    mustrain_axes = parse_axes(args.mustrain_axis, len(cifs))
    atom_flags = parse_atom_flags(args.atom_flags, len(cifs))
    if any(atom_flags) and (
        not args.allow_atomic_refinement
        or not (args.atomic_justification or "").strip()
    ):
        raise SystemExit(
            "Sequential atomic X/U refinement requires both "
            "--allow-atomic-refinement and a nonblank --atomic-justification"
        )
    frames = load_manifest(
        manifest_path,
        phase_names,
        allow_missing_metadata=args.allow_missing_metadata,
    )
    pattern_preflight = preflight_patterns(
        frames,
        mode=args.pattern_preflight,
    )
    metadata_audit = audit_manifest_metadata(
        frames,
        file_order_only=args.allow_missing_metadata,
    )
    anchors = select_anchor_frames(frames, args.anchor_orders)
    limits = (args.two_theta_min, args.two_theta_max)
    plan = {
        "schema_version": 1,
        "sample_id": args.sample_id,
        "request_classification": request_classification,
        "manifest": {
            "path": str(manifest_path),
            "bytes": manifest_path.stat().st_size,
            "sha256": sha256(manifest_path),
        },
        "instrument": {
            "path": str(instrument),
            "bytes": instrument.stat().st_size,
            "sha256": sha256(instrument),
            "profile_status": args.instrument_profile_status,
        },
        "phases": [
            {
                "name": name,
                "cif": {
                    "path": str(cif),
                    "bytes": cif.stat().st_size,
                    "sha256": sha256(cif),
                },
                "hstrain_mask": mask,
                "size_model": size_model,
                "mustrain_model": mustrain_model,
                "mustrain_axis": mustrain_axis,
                "atom_flags": atom_flag or "none",
            }
            for (
                name,
                cif,
                mask,
                size_model,
                mustrain_model,
                mustrain_axis,
                atom_flag,
            ) in zip(
                phase_names,
                cifs,
                hstrain_masks,
                size_models,
                mustrain_models,
                mustrain_axes,
                atom_flags,
            )
        ],
        "settings": {
            "xrd_format": args.xrd_format,
            "cif_format": args.cif_format,
            "background_order": args.background_order,
            "limits": limits,
            "displacement_mode": args.displacement_mode,
            "goniometer_radius": args.goniometer_radius,
            "refine_displacement_in_sequence": (
                args.refine_displacement_in_sequence
            ),
            "phase_fractions": args.phase_fractions,
            "anchor_max_passes": args.anchor_max_passes,
            "max_shift_over_su": args.max_shift_over_su,
            "max_anchor_rwp_over_rmin": args.max_anchor_rwp_over_rmin,
            "max_cycles": args.max_cycles,
            "sequential_stage_max_passes": (
                args.sequential_stage_max_passes
            ),
            "instrument_profile_status": args.instrument_profile_status,
            "instrument_profile_refinement": "locked",
            "pattern_preflight": args.pattern_preflight,
            "intensity_weighting": args.intensity_weighting,
            "atomic_refinement_justification": (
                args.atomic_justification if any(atom_flags) else None
            ),
            "audit_tolerances": {
                "cell_relative_tolerance": args.cell_relative_tolerance,
                "volume_relative_tolerance": args.volume_relative_tolerance,
                "mass_fraction_tolerance": args.mass_fraction_tolerance,
                "rwp_tolerance": args.rwp_direction_tolerance,
                "residual_minimum_sigma": args.residual_minimum_sigma,
                "residual_minimum_pattern_percent": (
                    args.residual_minimum_pattern_percent
                ),
                "residual_hard_pattern_percent": (
                    args.residual_hard_pattern_percent
                ),
                "residual_two_theta_tolerance": (
                    args.residual_two_theta_tolerance
                ),
            },
        },
        "pattern_preflight": pattern_preflight,
        "metadata_audit": metadata_audit,
        "anchor_frames": [
            {"frame_id": frame["frame_id"], "order": frame["order"]}
            for frame in anchors
        ],
        "frames": frames,
        "sequence_runs": ["forward", "reverse"],
        "figure_generation": False,
    }
    if args.plan_only:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    gsasii_path = resolve_gsasii_path(args.gsasii_path)
    if str(gsasii_path) not in sys.path:
        sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIscriptable as G2sc  # type: ignore

    run_id = clean_name(
        args.run_id
        or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    staging_root = Path(args.staging_root).expanduser().resolve()
    run_dir = staging_root / f"{clean_name(args.sample_id)}_sequential_{run_id}"
    if run_dir.exists():
        raise SystemExit(f"Run staging directory already exists: {run_dir}")
    anchors_dir = run_dir / "anchors"
    sequences_dir = run_dir / "sequences"
    results_dir = run_dir / "results"
    inputs_dir = run_dir / "inputs"
    anchors_dir.mkdir(parents=True)
    sequences_dir.mkdir()
    results_dir.mkdir()
    inputs_dir.mkdir()

    anchor_results = []
    anchor_paths: dict[str, Path] = {}
    try:
        input_bundle = {
            "schema_version": 1,
            "manifest": copy_verified(
                manifest_path,
                inputs_dir / f"sequence_manifest{manifest_path.suffix}",
            ),
            "instrument": copy_verified(
                instrument,
                inputs_dir / f"instrument{instrument.suffix}",
            ),
            "phases": [
                copy_verified(
                    cif,
                    inputs_dir
                    / "phases"
                    / f"{index:02d}_{clean_name(name)}{cif.suffix}",
                )
                for index, (name, cif) in enumerate(
                    zip(phase_names, cifs), start=1
                )
            ],
            "patterns": [
                copy_verified(
                    Path(frame["pattern"]["path"]),
                    inputs_dir
                    / "patterns"
                    / (
                        f"{frame['order']:05d}_{clean_name(frame['frame_id'])}"
                        f"{Path(frame['pattern']['path']).suffix}"
                    ),
                )
                for frame in frames
            ],
        }
        input_bundle_path = run_dir / "input_bundle_manifest.json"
        write_json_atomic(input_bundle_path, input_bundle)
        for frame in anchors:
            anchor_path = anchors_dir / f"{clean_name(frame['frame_id'])}.gpx"
            result = build_anchor(
                G2sc=G2sc,
                frame=frame,
                output_path=anchor_path,
                cifs=cifs,
                phase_names=phase_names,
                instrument=instrument,
                xrd_format=args.xrd_format,
                cif_format=args.cif_format,
                background_order=args.background_order,
                limits=limits,
                displacement_mode=args.displacement_mode,
                goniometer_radius=args.goniometer_radius,
                maximum_passes=args.anchor_max_passes,
                max_shift_over_su=args.max_shift_over_su,
                max_rwp_over_rmin=args.max_anchor_rwp_over_rmin,
                refine_phase_fractions=args.phase_fractions == "refine",
                size_models=size_models,
                mustrain_models=mustrain_models,
                mustrain_axes=mustrain_axes,
            )
            result["gpx"] = {
                "path": str(anchor_path),
                "bytes": anchor_path.stat().st_size,
                "sha256": sha256(anchor_path),
            }
            anchor_results.append(result)
            anchor_paths[frame["frame_id"]] = anchor_path
        write_json_atomic(
            run_dir / "anchor_summary.json",
            {
                "schema_version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "anchors": anchor_results,
            },
        )
        endpoint_ids = {
            frames[0]["frame_id"],
            frames[-1]["frame_id"],
        }
        rejected_anchors = []
        accepted_anchor_ids = set()
        for result in anchor_results:
            if anchor_gate_passes(
                result,
                max_shift_over_su=args.max_shift_over_su,
                max_rwp_over_rmin=args.max_anchor_rwp_over_rmin,
            ):
                accepted_anchor_ids.add(result["frame_id"])
            else:
                rejected_anchors.append(
                    {
                        "frame_id": result["frame_id"],
                        "order": result["order"],
                        "endpoint": result["frame_id"] in endpoint_ids,
                        "convergence": result["convergence"],
                        "metrics": result["metrics"],
                    }
                )
        rejected_endpoints = [
            item for item in rejected_anchors if item["endpoint"]
        ]
        if rejected_endpoints:
            raise RuntimeError(
                "Endpoint anchor gate failed; do not start a warm-start "
                "sequence from an unstable representative fit: "
                + json.dumps(rejected_endpoints, ensure_ascii=False)
            )
        plan["anchor_gate"] = {
            "accepted_frame_ids": sorted(accepted_anchor_ids),
            "rejected": rejected_anchors,
            "policy": (
                "Rejected non-endpoint anchors remain diagnostic only. "
                "Accepted anchors are actual propagation checkpoints."
            ),
        }

        reference_frame_id = frames[0]["frame_id"]
        reference_cells = read_reference_cells(
            G2sc,
            anchor_paths[reference_frame_id],
            phase_names,
        )
        plan["common_reference_cell"] = {
            "source_frame_id": reference_frame_id,
            "source_anchor": str(anchor_paths[reference_frame_id]),
            "phases": {
                name: {
                    "refine_flag": bool(cell[0]),
                    **{
                        label: float(value)
                        for label, value in zip(CELL_LABELS, cell[1:])
                    },
                }
                for name, cell in reference_cells.items()
            },
            "purpose": (
                "Use identical fixed global-cell components in forward and "
                "reverse runs; per-frame enabled HStrain components remain "
                "refinable."
            ),
        }
        segment_definitions = {
            direction: build_anchor_segments(
                frames,
                accepted_anchor_ids,
                direction=direction,
            )
            for direction in ("forward", "reverse")
        }
        runtime_segments: dict[str, list[dict[str, Any]]] = {
            "forward": [],
            "reverse": [],
        }
        plan["checkpoint_segments"] = {"forward": [], "reverse": []}
        for direction in ("forward", "reverse"):
            for definition in segment_definitions[direction]:
                segment_id = definition["segment_id"]
                if (
                    args.phase_fractions == "refine"
                    and any(
                        frame["phase_set"]
                        != definition["frames"][0]["phase_set"]
                        for frame in definition["frames"][1:]
                    )
                ):
                    raise RuntimeError(
                        f"{segment_id} crosses a phase_set change. Supply "
                        "stable transition anchors or keep phase fractions fixed."
                    )
                segment_dir = sequences_dir / segment_id
                segment_dir.mkdir()
                base_path = segment_dir / "sequence_base.gpx"
                project, histogram_names, seed_hstrain_offsets = (
                    prepare_sequence_base(
                        G2sc=G2sc,
                        seed_anchor_path=anchor_paths[
                            definition["checkpoint_frame_id"]
                        ],
                        base_path=base_path,
                        frames=definition["frames"],
                        phase_names=phase_names,
                        instrument=instrument,
                        xrd_format=args.xrd_format,
                        background_order=args.background_order,
                        limits=limits,
                        displacement_mode=args.displacement_mode,
                        goniometer_radius=args.goniometer_radius,
                        refine_displacement_in_sequence=False,
                        refine_phase_fractions=False,
                        hstrain_masks=hstrain_masks,
                        max_cycles=args.max_cycles,
                        reference_cells=reference_cells,
                    )
                )
                del project
                frame_snapshot = json.loads(
                    json.dumps(definition["frames"], ensure_ascii=False)
                )
                output_path = segment_dir / "sequential.gpx"
                record = {
                    "segment_id": segment_id,
                    "checkpoint_frame_id": definition[
                        "checkpoint_frame_id"
                    ],
                    "frames": frame_snapshot,
                    "base_path": base_path,
                    "output_path": output_path,
                }
                runtime_segments[direction].append(record)
                plan["checkpoint_segments"][direction].append(
                    {
                        "segment_id": segment_id,
                        "checkpoint_frame_id": definition[
                            "checkpoint_frame_id"
                        ],
                        "frame_ids": [
                            frame["frame_id"] for frame in frame_snapshot
                        ],
                        "histogram_order": histogram_names,
                        "seed_hstrain_offsets": seed_hstrain_offsets,
                        "base": {
                            "path": str(base_path),
                            "sha256": sha256(base_path),
                        },
                    }
                )
        plan["frames"] = [
            {key: value for key, value in frame.items() if key != "histogram"}
            for frame in frames
        ]
        plan["run_dir"] = str(run_dir)
        plan["input_bundle"] = {
            "path": str(input_bundle_path),
            "sha256": sha256(input_bundle_path),
        }
        manifest_json = run_dir / "sequence_manifest.json"
        write_json_atomic(manifest_json, plan)

        for direction in ("forward", "reverse"):
            for record in runtime_segments[direction]:
                record["run"] = run_sequence(
                    G2sc=G2sc,
                    base_path=record["base_path"],
                    output_path=record["output_path"],
                    reverse=direction == "reverse",
                    max_cycles=args.max_cycles,
                    stage_maximum_passes=(
                        args.sequential_stage_max_passes
                    ),
                    phase_names=phase_names,
                    refine_displacement=args.refine_displacement_in_sequence,
                    refine_phase_fractions=args.phase_fractions == "refine",
                    size_models=size_models,
                    mustrain_models=mustrain_models,
                    mustrain_axes=mustrain_axes,
                    atom_flags=atom_flags,
                )
        audit_tolerances = plan["settings"]["audit_tolerances"]
        audit_outputs = materialize_segmented_audit(
            manifest_path=manifest_json,
            forward_segments=runtime_segments["forward"],
            reverse_segments=runtime_segments["reverse"],
            output_dir=results_dir,
            gsasii_path=gsasii_path,
            audit_tolerances=audit_tolerances,
        )
        summary = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sample_id": args.sample_id,
            "run_dir": str(run_dir),
            "manifest": {
                "path": str(manifest_json),
                "sha256": sha256(manifest_json),
            },
            "anchor_summary": {
                "path": str(run_dir / "anchor_summary.json"),
                "sha256": sha256(run_dir / "anchor_summary.json"),
            },
            "input_bundle": {
                "path": str(input_bundle_path),
                "sha256": sha256(input_bundle_path),
            },
            "checkpoint_segments": {
                direction: [
                    {
                        "segment_id": record["segment_id"],
                        "checkpoint_frame_id": record[
                            "checkpoint_frame_id"
                        ],
                        "frame_ids": [
                            frame["frame_id"] for frame in record["frames"]
                        ],
                        "base": {
                            "path": str(record["base_path"]),
                            "sha256": sha256(record["base_path"]),
                        },
                        "run": record["run"],
                    }
                    for record in runtime_segments[direction]
                ]
                for direction in ("forward", "reverse")
            },
            "forward": {
                "segment_count": len(runtime_segments["forward"]),
                "complete": all(
                    record["run"]["complete"]
                    for record in runtime_segments["forward"]
                ),
            },
            "reverse": {
                "segment_count": len(runtime_segments["reverse"]),
                "complete": all(
                    record["run"]["complete"]
                    for record in runtime_segments["reverse"]
                ),
            },
            "audit_outputs": {
                role: {"path": str(path), "sha256": sha256(path)}
                for role, path in audit_outputs.items()
            },
            "figure_generated": False,
        }
        summary_path = run_dir / "sequence_run_summary.json"
        write_json_atomic(summary_path, summary)
    except Exception as exc:
        failure_path = run_dir / "RUN_FAILED.json"
        write_json_atomic(
            failure_path,
            {
                "schema_version": 1,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "run_dir": str(run_dir),
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "message": (
                    "The run stopped without deleting intermediate evidence. "
                    "Inspect console output and the files already written."
                ),
            },
        )
        raise

    print(run_dir)
    print(summary_path)
    print(results_dir / "sequential_audit.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
