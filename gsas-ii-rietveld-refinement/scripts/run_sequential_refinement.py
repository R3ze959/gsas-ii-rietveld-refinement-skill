#!/usr/bin/env python3
"""Run deterministic forward/reverse GSAS-II sequential powder refinements."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from refinement_audit import (
    _covariance_correlations,
    default_data_root,
    format_value_esd,
    resolve_gsasii_path,
    sha256,
    write_json_atomic,
)
from sequential_audit import CELL_LABELS, materialize_audit


DEFAULT_STAGING_ROOT = default_data_root(
    "GSASII_REFINEMENT_STAGING", "GSAS-II_refinement_staging"
)
CORE_MANIFEST_FIELDS = {"frame_id", "pattern_path", "order", "phase_set"}
STANDARD_METADATA_FIELDS = (
    "time_s",
    "temperature_K",
    "voltage_V",
    "current_mA",
    "capacity_mAh",
    "state_of_charge",
)


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


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise SystemExit(f"{label} is missing or empty: {path}")
    return path


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
    return collect_anchor(project, frame, passes)


def select_anchor_frames(
    frames: list[dict[str, Any]], requested_orders: str | None
) -> list[dict[str, Any]]:
    by_order = {frame["order"]: frame for frame in frames}
    if requested_orders:
        orders = [frames[0]["order"], frames[-1]["order"]]
        for item in requested_orders.split(","):
            try:
                order = int(item.strip())
            except ValueError as exc:
                raise SystemExit(
                    f"Invalid --anchor-orders value: {item!r}"
                ) from exc
            if order not in by_order:
                raise SystemExit(f"Anchor order is absent from manifest: {order}")
            if order not in orders:
                orders.append(order)
        return [by_order[order] for order in sorted(orders)]
    indices = sorted({0, len(frames) // 2, len(frames) - 1})
    return [frames[index] for index in indices]


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
) -> tuple[Any, list[str]]:
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
    for phase in project.phases():
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

    if "Constraints" in project.data:
        project.data["Constraints"]["data"]["_seqmode"] = "auto-wildcard"
    project.set_Controls("cycles", max_cycles)
    project.set_Controls("sequential", project.histograms())
    project.set_Controls("seqCopy", True)
    project.set_Controls("Reverse Seq", False)
    project.save(str(base_path))
    return project, [histogram.name for histogram in project.histograms()]


def run_sequence(
    *,
    G2sc: Any,
    base_path: Path,
    output_path: Path,
    reverse: bool,
    max_cycles: int,
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

    def execute_stage(name: str, required_tokens: tuple[str, ...] = ()) -> None:
        project.refine()
        project.save(str(output_path))
        sequence = project.seqref()
        completed = sequence.histograms() if sequence is not None else []
        stage_failures = []
        if sequence is not None:
            for histogram_name in completed:
                data = sequence.data[histogram_name]
                r_values = data.get("Rvals", {})
                if not r_values.get("converged", False):
                    stage_failures.append(
                        f"{histogram_name}: not converged"
                    )
                if int(r_values.get("SVD0", 0) or 0):
                    stage_failures.append(
                        f"{histogram_name}: SVD0={r_values['SVD0']}"
                    )
                vary_list = [str(item) for item in data.get("varyList", [])]
                for token in required_tokens:
                    if not any(token in variable for variable in vary_list):
                        stage_failures.append(
                            f"{histogram_name}: required variable family "
                            f"{token!r} is absent"
                        )
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
            }
        )
        if set(expected) != set(completed):
            raise RuntimeError(
                f"Sequential stage {name} completed {len(completed)} of "
                f"{len(expected)} histograms"
            )
        if stage_failures:
            raise RuntimeError(
                f"Sequential stage {name} failed validation: "
                + "; ".join(stage_failures)
            )

    # Stage 1: only the stable terms configured in the base project
    # (background, histogram scale and HAP Dij/HStrain) are active.
    execute_stage("stage1_stable")

    if refine_displacement:
        for histogram in project.histograms():
            if "DisplaceX" in histogram.data["Sample Parameters"]:
                histogram.set_refinements(
                    {"Sample Parameters": ["DisplaceX"]}
                )
        project.set_Controls("seqCopy", False)
        execute_stage("stage2_geometry", ("DisplaceX",))

    complex_stage = refine_phase_fractions or any(
        model != "off" for model in size_models + mustrain_models
    )
    if complex_stage:
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
        execute_stage("stage3_phase_profile", tuple(required))

    if any(atom_flags):
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
        execute_stage("stage4_atoms", tuple(required))

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
    parser.add_argument("--allow-missing-metadata", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    if not 2 <= args.background_order <= 20:
        raise SystemExit("--background-order must be between 2 and 20")
    if not 1 <= args.anchor_max_passes <= 20:
        raise SystemExit("--anchor-max-passes must be between 1 and 20")
    if not 1 <= args.max_cycles <= 50:
        raise SystemExit("--max-cycles must be between 1 and 50")
    if args.max_shift_over_su <= 0:
        raise SystemExit("--max-shift-over-su must be positive")
    if args.max_anchor_rwp_over_rmin <= 1:
        raise SystemExit("--max-anchor-rwp-over-rmin must be greater than 1")
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
    frames = load_manifest(
        manifest_path,
        phase_names,
        allow_missing_metadata=args.allow_missing_metadata,
    )
    anchors = select_anchor_frames(frames, args.anchor_orders)
    limits = (args.two_theta_min, args.two_theta_max)
    plan = {
        "schema_version": 1,
        "sample_id": args.sample_id,
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
            "instrument_profile_status": args.instrument_profile_status,
            "instrument_profile_refinement": "locked",
        },
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
        rejected_endpoints = []
        for result in anchor_results:
            if result["frame_id"] not in endpoint_ids:
                continue
            convergence = result["convergence"]
            shift = convergence.get("max_shift_over_su")
            quality_ratio = result["metrics"].get("Rwp_over_Rwp_min")
            if (
                not convergence.get("converged", False)
                or convergence.get("SVD0", 0)
                or shift is None
                or float(shift) > args.max_shift_over_su
                or quality_ratio is None
                or float(quality_ratio) > args.max_anchor_rwp_over_rmin
            ):
                rejected_endpoints.append(
                    {
                        "frame_id": result["frame_id"],
                        "convergence": convergence,
                        "metrics": result["metrics"],
                    }
                )
        if rejected_endpoints:
            raise RuntimeError(
                "Endpoint anchor gate failed; do not start a warm-start "
                "sequence from an unstable representative fit: "
                + json.dumps(rejected_endpoints, ensure_ascii=False)
            )

        forward_base_path = sequences_dir / "sequence_base_forward.gpx"
        project, forward_histogram_names = prepare_sequence_base(
            G2sc=G2sc,
            seed_anchor_path=anchor_paths[frames[0]["frame_id"]],
            base_path=forward_base_path,
            frames=frames,
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
        )
        del project
        reverse_frames = list(reversed(frames))
        reverse_base_path = sequences_dir / "sequence_base_reverse.gpx"
        project, reverse_histogram_names = prepare_sequence_base(
            G2sc=G2sc,
            seed_anchor_path=anchor_paths[frames[-1]["frame_id"]],
            base_path=reverse_base_path,
            frames=reverse_frames,
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
        )
        del project
        plan["frames"] = frames
        plan["histogram_order"] = {
            "forward": forward_histogram_names,
            "reverse": reverse_histogram_names,
        }
        plan["run_dir"] = str(run_dir)
        plan["input_bundle"] = {
            "path": str(input_bundle_path),
            "sha256": sha256(input_bundle_path),
        }
        manifest_json = run_dir / "sequence_manifest.json"
        write_json_atomic(manifest_json, plan)

        forward_path = sequences_dir / "sequential_forward.gpx"
        reverse_path = sequences_dir / "sequential_reverse.gpx"
        forward_run = run_sequence(
            G2sc=G2sc,
            base_path=forward_base_path,
            output_path=forward_path,
            reverse=False,
            max_cycles=args.max_cycles,
            phase_names=phase_names,
            refine_displacement=args.refine_displacement_in_sequence,
            refine_phase_fractions=args.phase_fractions == "refine",
            size_models=size_models,
            mustrain_models=mustrain_models,
            mustrain_axes=mustrain_axes,
            atom_flags=atom_flags,
        )
        reverse_run = run_sequence(
            G2sc=G2sc,
            base_path=reverse_base_path,
            output_path=reverse_path,
            reverse=True,
            max_cycles=args.max_cycles,
            phase_names=phase_names,
            refine_displacement=args.refine_displacement_in_sequence,
            refine_phase_fractions=args.phase_fractions == "refine",
            size_models=size_models,
            mustrain_models=mustrain_models,
            mustrain_axes=mustrain_axes,
            atom_flags=atom_flags,
        )
        audit_outputs = materialize_audit(
            manifest_path=manifest_json,
            forward_gpx=forward_path,
            reverse_gpx=reverse_path,
            output_dir=results_dir,
            gsasii_path=gsasii_path,
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
            "sequence_bases": {
                "forward": {
                    "path": str(forward_base_path),
                    "sha256": sha256(forward_base_path),
                },
                "reverse": {
                    "path": str(reverse_base_path),
                    "sha256": sha256(reverse_base_path),
                },
            },
            "forward": forward_run,
            "reverse": reverse_run,
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
