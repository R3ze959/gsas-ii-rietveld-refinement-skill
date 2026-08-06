#!/usr/bin/env python3
"""Run deterministic, multi-anchor real-pattern GSAS-II QPA."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_phase_models import audit_phase_models
from qpa_common_problems import (
    amorphous_from_internal_standard,
    amorphous_interval_from_internal_standard,
    assess_preferred_orientation_sensitivity,
    assess_trace_phases,
    microabsorption_multiplier_sensitivity,
    parse_hkl,
    parse_positive_interval,
    validate_common_problem_evidence,
)
from qpa_core import (
    assess_real_qpa_candidate,
    json_clean,
    maximum_correlation,
    normalized_mass_fractions_with_covariance,
    phase_set_completeness_gate,
    residual_peak_audit,
    scales_for_mass_fractions,
    select_competitive_candidate,
    sha256_file,
    summarize_qpa_repeatability,
    write_json_atomic,
)


def required_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def load_evidence_json(path: Path, label: str) -> dict[str, Any]:
    """Load one structured evidence contract without accepting free text."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return data


def calibration_record_from_summary(
    summary_path: Path, instrument: Path
) -> dict[str, Any]:
    """Validate and hash-bind a calibration summary to one instrument file."""
    try:
        calibration_data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read calibration summary: {exc}") from exc
    artifact = calibration_data.get("selected_profile_artifact") or {}
    if calibration_data.get("status") != "pass":
        raise ValueError("instrument calibration summary status is not pass")
    if artifact.get("sha256") != sha256_file(instrument):
        raise ValueError("instrument profile hash does not match calibration summary")
    return {
        "summary": {
            "path": str(summary_path),
            "sha256": sha256_file(summary_path),
        },
        "selected_profile_artifact": artifact,
        "selected_candidate": calibration_data.get("selected_candidate"),
    }


def resolve_gsasii_path(value: str | None) -> Path:
    candidates = []
    if value:
        candidates.append(Path(value).expanduser())
    if os.environ.get("GSASII_DIR"):
        candidates.append(Path(os.environ["GSASII_DIR"]).expanduser())
    candidates.extend([Path.home() / "g2main" / "GSAS-II", Path.home() / "GSAS-II"])
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "GSASII" / "GSASIIscriptable.py").is_file():
            return resolved
    raise FileNotFoundError("GSAS-II not found; set GSASII_DIR or pass --gsasii-path")


def parse_named_files(values: list[str], label: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use NAME=/path syntax: {value}")
        name, raw_path = (item.strip() for item in value.split("=", 1))
        if not name or name in parsed:
            raise ValueError(f"blank or duplicate phase name in {label}: {name!r}")
        parsed[name] = required_file(raw_path, f"{label} {name}")
    return parsed


def parse_phase_roles(values: list[str], phase_names: list[str]) -> dict[str, str]:
    """Assign every modeled phase to sample, hardware or internal standard."""
    roles = {name: "sample" for name in phase_names}
    assigned: set[str] = set()
    allowed = {"sample", "hardware", "internal_standard"}
    for value in values:
        if "=" not in value:
            raise ValueError(f"phase role must use NAME=ROLE syntax: {value}")
        name, role = (item.strip() for item in value.split("=", 1))
        if name not in roles:
            raise ValueError(f"phase role names an undeclared phase: {name!r}")
        if name in assigned:
            raise ValueError(f"duplicate phase role: {name!r}")
        if role not in allowed:
            raise ValueError(f"unsupported phase role for {name!r}: {role!r}")
        roles[name] = role
        assigned.add(name)
    if sum(role == "sample" for role in roles.values()) < 2:
        raise ValueError(
            "multiphase sample QPA requires at least two phases with role=sample"
        )
    return roles


def parse_named_axes(values: list[str], phase_names: list[str]) -> dict[str, tuple[int, int, int]]:
    """Parse one explicitly justified March-Dollase axis per declared phase."""
    parsed: dict[str, tuple[int, int, int]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"preferred-orientation axis must use NAME=h,k,l: {value}")
        name, raw_axis = (item.strip() for item in value.split("=", 1))
        if name not in phase_names:
            raise ValueError(f"preferred-orientation axis names an undeclared phase: {name!r}")
        if name in parsed:
            raise ValueError(f"duplicate preferred-orientation axis: {name!r}")
        parsed[name] = parse_hkl(raw_axis)
    return parsed


def parse_named_intervals(
    values: list[str], phase_names: list[str], label: str
) -> dict[str, tuple[float, float]]:
    """Parse complete phase-indexed positive sensitivity intervals."""
    parsed: dict[str, tuple[float, float]] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"{label} must use NAME=LOW,HIGH: {value}")
        name, raw_interval = (item.strip() for item in value.split("=", 1))
        if name not in phase_names:
            raise ValueError(f"{label} names an undeclared phase: {name!r}")
        if name in parsed:
            raise ValueError(f"duplicate {label}: {name!r}")
        parsed[name] = parse_positive_interval(raw_interval)
    return parsed


def starting_compositions(
    phase_names: list[str], values: list[str]
) -> list[dict[str, float]]:
    if values:
        output = []
        for value in values:
            numbers = [float(item.strip()) for item in value.split(",")]
            if len(numbers) != len(phase_names):
                raise ValueError(
                    f"--initial-composition needs {len(phase_names)} comma-separated values"
                )
            if any(item <= 0.0 or not math.isfinite(item) for item in numbers):
                raise ValueError("initial compositions must be finite and strictly positive")
            total = sum(numbers)
            output.append({name: number / total for name, number in zip(phase_names, numbers)})
    else:
        count = len(phase_names)
        output = [{name: 1.0 / count for name in phase_names}]
        if count > 1:
            minor = 0.2 / (count - 1)
            for major in phase_names:
                output.append({name: 0.8 if name == major else minor for name in phase_names})
    unique = []
    seen = set()
    for item in output:
        key = tuple(round(item[name], 12) for name in phase_names)
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique


def clean_component(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).strip("_")


def clear_profile(histogram: Any) -> None:
    histogram.clear_refinements(
        {"Instrument Parameters": ["Zero", "U", "V", "W", "X", "Y", "Z", "SH/L"]}
    )


SAMPLE_PARAMETERS_TO_CLEAR = (
    "Scale",
    "Shift",
    "DisplaceX",
    "DisplaceY",
    "Transparency",
    "Absorption",
    "SurfRoughA",
    "SurfRoughB",
)


def available_sample_parameters(histogram: Any, requested: tuple[str, ...]) -> list[str]:
    """Return only mutable sample parameters present for this geometry."""
    sample = histogram.data.get("Sample Parameters", {})
    return [
        name
        for name in requested
        if isinstance(sample.get(name), list) and len(sample[name]) >= 2
    ]


def sample_position_parameter(histogram: Any) -> str | None:
    """Choose one geometry-compatible position term without inventing Shift."""
    available = available_sample_parameters(
        histogram, ("Shift", "DisplaceX", "DisplaceY")
    )
    return available[0] if available else None


def sample_position_value(
    histogram: Any, parameter: str | None = None
) -> float | None:
    parameter = parameter or sample_position_parameter(histogram)
    if parameter is None:
        return None
    return float(histogram.data["Sample Parameters"][parameter][0])


def refine_sample_position(histogram: Any) -> str | None:
    parameter = sample_position_parameter(histogram)
    if parameter is not None:
        histogram.set_refinements({"Sample Parameters": [parameter]})
    return parameter


def clear_sample_parameter_refinements(histogram: Any) -> None:
    available = available_sample_parameters(histogram, SAMPLE_PARAMETERS_TO_CLEAR)
    if available:
        histogram.clear_refinements({"Sample Parameters": available})


def configure_histogram(
    histogram: Any,
    *,
    background_order: int,
    limits: tuple[float, float],
) -> None:
    histogram.set_refinements({"Limits": list(limits)})
    clear_sample_parameter_refinements(histogram)
    histogram.clear_refinements({"Background": True})
    clear_profile(histogram)
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


def configure_phase(phase: Any, histogram: Any) -> None:
    phase.clear_HAP_refinements(
        {"Scale": True, "Mustrain": True, "Size": True, "Pref.Ori.": True, "HStrain": True},
        [histogram],
    )
    phase.set_refinements({"Cell": False, "Atoms": {"all": ""}})


def refine_until(
    project: Any, *, max_passes: int, shift_limit: float
) -> tuple[int, dict[str, Any]]:
    r_values: dict[str, Any] = {}
    for index in range(max_passes):
        project.refine()
        project.save()
        covariance = project.data.get("Covariance", {}).get("data", {})
        r_values = covariance.get("Rvals", {})
        if not r_values or "covMatrix" not in covariance:
            raise RuntimeError("GSAS-II refinement produced no usable covariance result")
        shift = r_values.get("Max shft/sig")
        if (
            r_values.get("converged", False)
            and shift is not None
            and abs(float(shift)) <= shift_limit
        ):
            return index + 1, r_values
    return max_passes, r_values


def covariance_metrics(project: Any) -> dict[str, Any]:
    covariance = project.data.get("Covariance", {}).get("data", {})
    r_values = covariance.get("Rvals", {})
    vary_list = list(covariance.get("varyList", []))
    return json_clean(
        {
            "Rwp_percent": r_values.get("Rwp"),
            "GOF": r_values.get("GOF"),
            "converged": bool(r_values.get("converged", False)),
            "svd_count": int(r_values.get("SVD0", 0) or 0),
            "max_shift_over_esd": r_values.get("Max shft/sig"),
            "varying_parameter_count": len(vary_list),
            "varying_parameters": vary_list,
            "maximum_correlation": maximum_correlation(
                vary_list, covariance.get("covMatrix", [])
            ),
        }
    )


def phase_subset_mass_fractions(
    project: Any,
    histogram: Any,
    phase_objects: dict[str, Any],
    phase_names: list[str],
) -> dict[str, dict[str, float]]:
    """Compute covariance-backed fractions normalized over a declared subset."""
    covariance_data = project.data.get("Covariance", {}).get("data", {})
    covariance = covariance_data.get("covMatrix")
    vary_list = list(covariance_data.get("varyList", []))
    if covariance is None:
        raise RuntimeError("GSAS-II produced no covariance for role normalization")
    histogram_id = histogram.data["data"][0]["hId"]
    scales = {
        name: float(
            phase_objects[name].HAPvalue("Scale", targethistlist=[histogram])
        )
        for name in phase_names
    }
    masses = {
        name: float(phase_objects[name].data["General"]["Mass"])
        for name in phase_names
    }
    indices = {}
    for name in phase_names:
        parameter = f"{phase_objects[name].id}:{histogram_id}:Scale"
        if parameter in vary_list:
            indices[name] = vary_list.index(parameter)
    return normalized_mass_fractions_with_covariance(
        scales=scales,
        masses=masses,
        varying_scale_indices=indices,
        covariance=covariance,
    )


def assess_sample_normalization(
    candidate: dict[str, Any], assessment: dict[str, Any]
) -> dict[str, Any]:
    """Add role-normalized value and uncertainty checks to a fit assessment."""
    hard_failures = list(assessment.get("hard_failures", []))
    review_flags = list(assessment.get("review_flags", []))
    roles = candidate.get("phase_roles", {})
    expected = {name for name, role in roles.items() if role == "sample"}
    fractions = candidate.get("sample_normalized_mass_fractions", {})
    values = [item.get("value") for item in fractions.values()]
    if (
        set(fractions) != expected
        or not values
        or any(
            value is None or not math.isfinite(float(value)) or float(value) < 0.0
            for value in values
        )
        or not math.isclose(
            sum(float(value) for value in values if value is not None),
            1.0,
            abs_tol=1e-5,
        )
    ):
        hard_failures.append("invalid_sample_normalized_mass_fractions")
    for name, item in fractions.items():
        esd = item.get("esd")
        if esd is None or not math.isfinite(float(esd)) or float(esd) <= 0.0:
            review_flags.append(f"missing_or_nonpositive_sample_esd:{name}")
    return {
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "hard_failures": list(dict.fromkeys(hard_failures)),
        "review_flags": list(dict.fromkeys(review_flags)),
    }


def broadening_value(phase: Any, histogram: Any, kind: str) -> float:
    return float(phase.data["Histograms"][histogram.name][kind][1][0])


def set_fixed_broadening(phase: Any, histogram: Any, prior: dict[str, Any] | None) -> None:
    if not prior or prior.get("selected_model") == "locked":
        return
    kind = prior["selected_model"]
    if kind not in {"Size", "Mustrain"}:
        raise ValueError(f"unsupported transferred broadening model: {kind}")
    hap = phase.data["Histograms"][histogram.name][kind]
    hap[1][0] = float(prior["value"])
    hap[2][0] = False


def run_pure_prior(
    *,
    G2sc: Any,
    phase_name: str,
    pure_pattern: Path,
    cif: Path,
    instrument: Path,
    output_dir: Path,
    pattern_format: str,
    background_order: int,
    limits: tuple[float, float],
    max_passes: int,
    shift_limit: float,
    correlation_limit: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    baseline_path = output_dir / "baseline.gpx"
    project = G2sc.G2Project(newgpx=str(baseline_path))
    histogram = project.add_powder_histogram(
        str(pure_pattern), str(instrument), fmthint=pattern_format
    )
    phase = project.add_phase(str(cif), phasename=phase_name, histograms=[histogram])
    configure_histogram(histogram, background_order=background_order, limits=limits)
    configure_phase(phase, histogram)
    phase.HAPvalue("Scale", 1.0, [histogram])
    project.set_Controls("cycles", 12)
    scale_passes, _ = refine_until(project, max_passes=max_passes, shift_limit=shift_limit)
    position_parameter = refine_sample_position(histogram)
    shift_passes = 0
    if position_parameter is not None:
        shift_passes, _ = refine_until(
            project, max_passes=max_passes, shift_limit=shift_limit
        )
    project.save(str(baseline_path))

    records = []
    for model in ("locked", "Size", "Mustrain", "both"):
        candidate_path = baseline_path if model == "locked" else output_dir / f"{model}.gpx"
        if model != "locked":
            shutil.copy2(baseline_path, candidate_path)
        candidate_project = G2sc.G2Project(str(candidate_path))
        candidate_histogram = candidate_project.histograms()[0]
        candidate_phase = candidate_project.phases()[0]
        candidate_phase.clear_HAP_refinements({"Size": True, "Mustrain": True}, [candidate_histogram])
        if model in {"Size", "both"}:
            candidate_phase.set_HAP_refinements({"Size": {"refine": True}}, [candidate_histogram])
        if model in {"Mustrain", "both"}:
            candidate_phase.set_HAP_refinements({"Mustrain": {"refine": True}}, [candidate_histogram])
        passes, _ = refine_until(
            candidate_project, max_passes=max_passes, shift_limit=shift_limit
        )
        metrics = covariance_metrics(candidate_project)
        correlation = metrics["maximum_correlation"]["absolute"]
        safe = (
            metrics["converged"]
            and metrics["svd_count"] == 0
            and metrics["max_shift_over_esd"] is not None
            and abs(float(metrics["max_shift_over_esd"])) <= shift_limit
            and correlation is not None
            and float(correlation) < correlation_limit
        )
        record = {
            "model": model,
            "safe": safe,
            "refinement_passes": passes,
            "metrics": metrics,
            "values": {
                "Size": broadening_value(candidate_phase, candidate_histogram, "Size"),
                "Mustrain": broadening_value(candidate_phase, candidate_histogram, "Mustrain"),
            },
            "gpx": {"path": str(candidate_path), "sha256": sha256_file(candidate_path)},
        }
        records.append(record)
        write_json_atomic(output_dir / f"{model}.json", record)

    by_name = {record["model"]: record for record in records}
    safe_single = [by_name[name] for name in ("Size", "Mustrain") if by_name[name]["safe"]]
    if safe_single:
        selected = min(safe_single, key=lambda item: (float(item["metrics"]["GOF"]), item["model"]))
    elif by_name["locked"]["safe"]:
        selected = by_name["locked"]
    else:
        selected = None
    both = by_name["both"]
    if selected and both["safe"]:
        both_correlation = float(both["metrics"]["maximum_correlation"]["absolute"])
        if (
            both_correlation < 0.90
            and float(both["metrics"]["GOF"]) <= 0.98 * float(selected["metrics"]["GOF"])
        ):
            selected = both
    # A two-parameter result is retained for diagnosis but not transferred; the
    # production driver accepts only a single, portable broadening prior.
    if selected and selected["model"] == "both":
        selected = min(safe_single, key=lambda item: (float(item["metrics"]["GOF"]), item["model"]))
    structure = None
    if selected:
        structure_gpx = output_dir / "structure_U.gpx"
        shutil.copy2(Path(selected["gpx"]["path"]), structure_gpx)
        structure_project = G2sc.G2Project(str(structure_gpx))
        structure_histogram = structure_project.histograms()[0]
        structure_phase = structure_project.phases()[0]
        structure_phase.clear_HAP_refinements(
            {"Size": True, "Mustrain": True}, [structure_histogram]
        )
        structure_phase.set_refinements({"Cell": False, "Atoms": {"all": "U"}})
        structure_passes, _ = refine_until(
            structure_project, max_passes=max_passes, shift_limit=shift_limit
        )
        structure_metrics = covariance_metrics(structure_project)
        atom_pointer = int(structure_phase.data["General"]["AtomPtrs"][3])
        u_values = {
            str(atom[0]): float(atom[atom_pointer + 1])
            for atom in structure_phase.data["Atoms"]
        }
        structure_cif = output_dir / "refined_U.cif"
        structure_phase.export_CIF(str(structure_cif))
        structure_correlation = structure_metrics["maximum_correlation"]["absolute"]
        structure_safe = (
            structure_metrics["converged"]
            and structure_metrics["svd_count"] == 0
            and structure_metrics["max_shift_over_esd"] is not None
            and abs(float(structure_metrics["max_shift_over_esd"])) <= shift_limit
            and structure_correlation is not None
            and float(structure_correlation) < correlation_limit
            and all(0.0 < value < 0.1 for value in u_values.values())
        )
        structure = {
            "status": "pass" if structure_safe else "review",
            "atom_flags": "U",
            "refinement_passes": structure_passes,
            "metrics": structure_metrics,
            "u_iso_angstrom_squared": u_values,
            "physical_u_gate": "0 < Uiso < 0.1 Angstrom^2 for every atom",
            "gpx": {"path": str(structure_gpx), "sha256": sha256_file(structure_gpx)},
            "cif": {"path": str(structure_cif), "sha256": sha256_file(structure_cif)},
        }
        write_json_atomic(output_dir / "structure_U.json", structure)
    result_status = (
        "fail" if selected is None else "pass" if structure and structure["status"] == "pass" else "review"
    )
    result = {
        "phase": phase_name,
        "status": result_status,
        "selection_policy": "best safe single Size/Mustrain model; two-parameter model requires >=2% GOF improvement and correlation <0.90, but is diagnostic-only and not transferred",
        "baseline_passes": {"scale_background": scale_passes, "sample_shift": shift_passes},
        "selected_model": selected["model"] if selected else None,
        "value": (
            selected["values"][selected["model"]]
            if selected and selected["model"] in {"Size", "Mustrain"}
            else None
        ),
        "structure_refinement": structure,
        "refined_structure_cif": (
            structure["cif"] if structure and structure["status"] == "pass" else None
        ),
        "candidates": records,
    }
    write_json_atomic(output_dir / "pure_phase_prior.json", result)
    return result


def run_mixture_candidate(
    *,
    G2sc: Any,
    pattern: Path,
    instrument: Path,
    phases: dict[str, Path],
    phase_roles: dict[str, str],
    pure_priors: dict[str, dict[str, Any]],
    transfer_broadening: bool,
    anchor: str,
    initial: dict[str, float],
    initial_index: int,
    candidate_label: str | None,
    output_dir: Path,
    pattern_format: str,
    background_order: int,
    limits: tuple[float, float],
    max_passes: int,
    shift_limit: float,
    assessment_settings: dict[str, float],
    initial_hap_scales: dict[str, float] | None = None,
    initial_histogram_scale: float | None = None,
    initial_sample_shift: float | None = None,
    preferred_orientation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_name = candidate_label or f"anchor_{clean_component(anchor)}_start_{initial_index:02d}"
    output_dir.mkdir(parents=True, exist_ok=False)
    gpx_path = output_dir / "fit.gpx"
    try:
        project = G2sc.G2Project(newgpx=str(gpx_path))
        histogram = project.add_powder_histogram(
            str(pattern), str(instrument), fmthint=pattern_format
        )
        phase_objects = {
            name: project.add_phase(str(cif), phasename=name, histograms=[histogram])
            for name, cif in phases.items()
        }
        configure_histogram(histogram, background_order=background_order, limits=limits)
        masses = {
            name: float(phase.data["General"]["Mass"])
            for name, phase in phase_objects.items()
        }
        if initial_hap_scales is None:
            scales = scales_for_mass_fractions(initial, masses, anchor)
        else:
            if set(initial_hap_scales) != set(phases):
                raise ValueError("initial HAP scales do not match the declared phases")
            if any(
                not math.isfinite(float(value)) or float(value) <= 0.0
                for value in initial_hap_scales.values()
            ):
                raise ValueError("initial HAP scales must be finite and positive")
            scales = {name: float(value) for name, value in initial_hap_scales.items()}
        if initial_histogram_scale is not None:
            if not math.isfinite(initial_histogram_scale) or initial_histogram_scale <= 0.0:
                raise ValueError("initial histogram scale must be finite and positive")
            histogram.data["Sample Parameters"]["Scale"][0] = float(
                initial_histogram_scale
            )
        position_parameter = sample_position_parameter(histogram)
        if initial_sample_shift is not None and position_parameter is not None:
            if not math.isfinite(initial_sample_shift):
                raise ValueError("initial sample position must be finite")
            histogram.data["Sample Parameters"][position_parameter][0] = float(
                initial_sample_shift
            )
        starting_histogram_scale = float(
            histogram.data["Sample Parameters"]["Scale"][0]
        )
        starting_sample_position = sample_position_value(
            histogram, position_parameter
        )
        for name, phase in phase_objects.items():
            configure_phase(phase, histogram)
            phase.HAPvalue("Scale", scales[name], [histogram])
            if transfer_broadening:
                set_fixed_broadening(phase, histogram, pure_priors.get(name))
            if preferred_orientation and name == preferred_orientation["phase"]:
                axis = [int(item) for item in preferred_orientation["axis"]]
                phase.HAPvalue("PO", 1, [histogram])
                po_data = phase.data["Histograms"][histogram.name]["Pref.Ori."]
                po_data[0] = "MD"
                po_data[1] = 1.0
                po_data[2] = True
                po_data[3] = axis
            if name != anchor:
                phase.set_HAP_refinements({"Scale": True}, [histogram])
        project.set_Controls("cycles", 12)
        scale_passes, _ = refine_until(
            project, max_passes=max_passes, shift_limit=shift_limit
        )
        position_parameter = refine_sample_position(histogram)
        shift_passes = 0
        if position_parameter is not None:
            shift_passes, _ = refine_until(
                project, max_passes=max_passes, shift_limit=shift_limit
            )
        refined_sample_position = sample_position_value(
            histogram, position_parameter
        )
        project.save(str(gpx_path))
        covariance = project.data.get("Covariance", {}).get("data", {})
        if "covMatrix" not in covariance:
            raise RuntimeError("GSAS-II produced no covariance matrix for final QPA")
        fractions = histogram.ComputeMassFracs()
        sample_phase_names = [
            name for name in phases if phase_roles[name] == "sample"
        ]
        sample_fractions = phase_subset_mass_fractions(
            project, histogram, phase_objects, sample_phase_names
        )
        quantitative_phase_names = [
            name for name in phases if phase_roles[name] != "hardware"
        ]
        quantitative_fractions = phase_subset_mass_fractions(
            project, histogram, phase_objects, quantitative_phase_names
        )
        metrics = covariance_metrics(project)
        preferred_orientation_record = None
        if preferred_orientation:
            phase_name = str(preferred_orientation["phase"])
            phase = phase_objects[phase_name]
            po_data = phase.data["Histograms"][histogram.name]["Pref.Ori."]
            md_parameter = f"{phase.id}:{histogram.data['data'][0]['hId']}:MD"
            covariance_data = project.data.get("Covariance", {}).get("data", {})
            vary_list = list(covariance_data.get("varyList", []))
            covariance_matrix = covariance_data.get("covMatrix", [])
            md_esd = None
            if md_parameter in vary_list:
                index = vary_list.index(md_parameter)
                variance = float(covariance_matrix[index][index])
                if variance >= 0.0:
                    md_esd = math.sqrt(variance)
            preferred_orientation_record = {
                "phase": phase_name,
                "model": "March-Dollase",
                "axis": [int(item) for item in po_data[3]],
                "ratio": float(po_data[1]),
                "esd": md_esd,
                "varying_parameter": md_parameter,
            }
        record = {
            "candidate": candidate_name,
            "anchor": anchor,
            "phase_roles": phase_roles,
            "initial_mass_fractions": initial,
            "phase_parameterization": {
                "fixed_hap_scale": {anchor: 1.0},
                "refined_hap_scales": [name for name in phases if name != anchor],
                "refined_histogram_scale": True,
                "refined_sample_shift": position_parameter == "Shift",
                "refined_sample_position_parameter": position_parameter,
                "cells_locked": True,
                "instrument_profile_locked": True,
                "pure_phase_broadening_transferred": transfer_broadening,
                "exact_restart_scales_used": initial_hap_scales is not None,
                "preferred_orientation_model": (
                    preferred_orientation_record["model"]
                    if preferred_orientation_record
                    else "locked_off"
                ),
            },
            "starting_hap_scales": scales,
            "starting_histogram_scale": starting_histogram_scale,
            "starting_sample_shift": (
                starting_sample_position if position_parameter == "Shift" else None
            ),
            "starting_sample_position": (
                {
                    "parameter": position_parameter,
                    "value": starting_sample_position,
                }
                if position_parameter is not None
                else None
            ),
            "refinement_passes": {
                "scale_background": scale_passes,
                "sample_shift": shift_passes,
            },
            "refined_mass_fractions": {
                name: {"value": float(value), "esd": float(esd)}
                for name, (value, esd) in fractions.items()
            },
            "sample_normalized_mass_fractions": sample_fractions,
            "quantitative_normalized_mass_fractions": quantitative_fractions,
            "refined_hap_scales": {
                name: float(phase.HAPvalue("Scale", targethistlist=[histogram]))
                for name, phase in phase_objects.items()
            },
            "refined_histogram_scale": float(histogram.data["Sample Parameters"]["Scale"][0]),
            "refined_sample_shift": (
                refined_sample_position if position_parameter == "Shift" else None
            ),
            "refined_sample_position": (
                {
                    "parameter": position_parameter,
                    "value": refined_sample_position,
                }
                if position_parameter is not None
                else None
            ),
            "transferred_broadening_priors": {
                name: {
                    "model": (
                        pure_priors.get(name, {}).get("selected_model", "locked")
                        if transfer_broadening
                        else "locked"
                    ),
                    "value": (
                        pure_priors.get(name, {}).get("value")
                        if transfer_broadening
                        else None
                    ),
                }
                for name in phases
            },
            "metrics": metrics,
            "preferred_orientation": preferred_orientation_record,
            "residual_audit": residual_peak_audit(histogram),
            "files": {
                "fit_gpx": str(gpx_path),
                "fit_lst": str(gpx_path.with_suffix(".lst")),
            },
        }
        record["assessment"] = assess_sample_normalization(
            record,
            assess_real_qpa_candidate(record, **assessment_settings),
        )
        record["status"] = record["assessment"]["status"]
    except Exception as exc:
        record = {
            "candidate": candidate_name,
            "anchor": anchor,
            "phase_roles": phase_roles,
            "initial_mass_fractions": initial,
            "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    write_json_atomic(output_dir / "candidate_result.json", record)
    return record


def clear_mixture_refinements(project: Any, histogram: Any) -> None:
    """Clear every mutable family before a controlled sensitivity branch."""
    clear_sample_parameter_refinements(histogram)
    histogram.clear_refinements({"Background": True})
    clear_profile(histogram)
    for phase in project.phases():
        configure_phase(phase, histogram)


def phase_cell_snapshot(phase: Any) -> dict[str, float]:
    cell = phase.data["General"]["Cell"]
    return {
        "a": float(cell[1]),
        "b": float(cell[2]),
        "c": float(cell[3]),
        "alpha": float(cell[4]),
        "beta": float(cell[5]),
        "gamma": float(cell[6]),
        "volume": float(cell[7]),
    }


def restart_composition(
    candidate: dict[str, Any], *, minimum_fraction: float
) -> dict[str, float]:
    """Create a positive restart near the selected model, not the distant seed."""
    if not 0.0 < minimum_fraction < 1.0:
        raise ValueError("repeatability restart floor must be between zero and one")
    values = {
        name: max(float(item["value"]), minimum_fraction)
        for name, item in candidate["refined_mass_fractions"].items()
    }
    total = sum(values.values())
    return {name: value / total for name, value in values.items()}


def candidate_sample_position_value(candidate: dict[str, Any]) -> float | None:
    """Read the geometry-aware position value with legacy Shift fallback."""
    position = candidate.get("refined_sample_position")
    if isinstance(position, dict) and position.get("value") is not None:
        return float(position["value"])
    legacy = candidate.get("refined_sample_shift")
    return None if legacy is None else float(legacy)


def run_controlled_cell_sensitivity(
    *,
    G2sc: Any,
    baseline: dict[str, Any],
    output_dir: Path,
    background_order: int,
    limits: tuple[float, float],
    max_passes: int,
    shift_limit: float,
    correlation_limit: float,
    minimum_fraction: float,
    maximum_phases: int,
    volume_change_limit: float,
    minimum_relative_gof_improvement: float,
    assessment_settings: dict[str, float],
) -> dict[str, Any]:
    """Refine abundant-phase cells one at a time, then lock them for QPA."""
    output_dir.mkdir(parents=True, exist_ok=False)
    fractions = baseline["sample_normalized_mass_fractions"]
    eligible = sorted(
        (
            (name, float(item["value"]))
            for name, item in fractions.items()
            if baseline["phase_roles"].get(name) == "sample"
            and float(item["value"]) >= minimum_fraction
        ),
        key=lambda item: (-item[1], item[0]),
    )[:maximum_phases]
    baseline_gpx = Path(baseline["files"]["fit_gpx"])
    current_gpx = output_dir / "00_locked_baseline.gpx"
    shutil.copy2(baseline_gpx, current_gpx)
    current_metrics = baseline["metrics"]
    steps = []
    accepted_phases: list[str] = []
    for index, (phase_name, abundance) in enumerate(eligible, start=1):
        trial_gpx = output_dir / f"{index:02d}_{clean_component(phase_name)}_cell.gpx"
        shutil.copy2(current_gpx, trial_gpx)
        project = G2sc.G2Project(str(trial_gpx))
        histogram = project.histograms()[0]
        clear_mixture_refinements(project, histogram)
        phase = project.phase(phase_name)
        before = phase_cell_snapshot(phase)
        phase.set_refinements({"Cell": True, "Atoms": {"all": ""}})
        passes, _ = refine_until(project, max_passes=max_passes, shift_limit=shift_limit)
        metrics = covariance_metrics(project)
        after = phase_cell_snapshot(phase)
        volume_change = abs(after["volume"] - before["volume"]) / before["volume"]
        prior_gof = float(current_metrics["GOF"])
        trial_gof = float(metrics["GOF"])
        gof_improvement = (prior_gof - trial_gof) / prior_gof
        correlation = metrics["maximum_correlation"]["absolute"]
        expected_cell_prefix = f"{phase.id}::A"
        cell_variables_present = any(
            str(parameter).startswith(expected_cell_prefix)
            for parameter in metrics["varying_parameters"]
        )
        correlation_safe = (
            metrics["varying_parameter_count"] <= 1
            or (correlation is not None and float(correlation) < correlation_limit)
        )
        safe = (
            metrics["converged"]
            and metrics["svd_count"] == 0
            and metrics["max_shift_over_esd"] is not None
            and abs(float(metrics["max_shift_over_esd"])) <= shift_limit
            and cell_variables_present
            and correlation_safe
            and volume_change <= volume_change_limit
            and gof_improvement >= minimum_relative_gof_improvement
        )
        reasons = []
        if not metrics["converged"]:
            reasons.append("nonconverged")
        if metrics["svd_count"]:
            reasons.append(f"svd_count={metrics['svd_count']}")
        if metrics["max_shift_over_esd"] is None or abs(float(metrics["max_shift_over_esd"])) > shift_limit:
            reasons.append("shift_over_esd_exceeds_limit")
        if not correlation_safe:
            reasons.append("high_or_unavailable_correlation")
        if not cell_variables_present:
            reasons.append("cell_variable_family_missing")
        if volume_change > volume_change_limit:
            reasons.append("cell_volume_change_exceeds_limit")
        if gof_improvement < minimum_relative_gof_improvement:
            reasons.append("gof_improvement_below_limit")
        phase.set_refinements({"Cell": False})
        project.save(str(trial_gpx))
        step = {
            "phase": phase_name,
            "baseline_fraction": abundance,
            "accepted": safe,
            "rejection_reasons": reasons,
            "refinement_passes": passes,
            "cell_before": before,
            "cell_after": after,
            "relative_volume_change": volume_change,
            "relative_gof_improvement": gof_improvement,
            "metrics": metrics,
            "expected_cell_variable_prefix": expected_cell_prefix,
            "gpx": {"path": str(trial_gpx), "sha256": sha256_file(trial_gpx)},
        }
        steps.append(step)
        if safe:
            current_gpx = trial_gpx
            current_metrics = metrics
            accepted_phases.append(phase_name)

    if not accepted_phases:
        result = {
            "status": "review",
            "candidate": None,
            "eligible_phases": [name for name, _ in eligible],
            "accepted_phases": [],
            "steps": steps,
            "review_flags": ["no_cell_sensitivity_step_accepted"],
        }
        write_json_atomic(output_dir / "cell_sensitivity_summary.json", result)
        return result

    final_gpx = output_dir / "final_cell_locked_composition.gpx"
    shutil.copy2(current_gpx, final_gpx)
    project = G2sc.G2Project(str(final_gpx))
    histogram = project.histograms()[0]
    phase_objects = {phase.name: phase for phase in project.phases()}
    configure_histogram(histogram, background_order=background_order, limits=limits)
    for name, phase in phase_objects.items():
        configure_phase(phase, histogram)
        if name != baseline["anchor"]:
            phase.set_HAP_refinements({"Scale": True}, [histogram])
    project.set_Controls("cycles", 12)
    scale_passes, _ = refine_until(project, max_passes=max_passes, shift_limit=shift_limit)
    position_parameter = refine_sample_position(histogram)
    shift_passes = 0
    if position_parameter is not None:
        shift_passes, _ = refine_until(
            project, max_passes=max_passes, shift_limit=shift_limit
        )
    refined_sample_position = sample_position_value(histogram, position_parameter)
    project.save(str(final_gpx))
    covariance = project.data.get("Covariance", {}).get("data", {})
    if "covMatrix" not in covariance:
        raise RuntimeError(
            "GSAS-II produced no covariance matrix after controlled cell sensitivity"
        )
    final_fractions = histogram.ComputeMassFracs()
    sample_phase_names = [
        name for name in phase_objects if baseline["phase_roles"][name] == "sample"
    ]
    sample_fractions = phase_subset_mass_fractions(
        project, histogram, phase_objects, sample_phase_names
    )
    quantitative_phase_names = [
        name for name in phase_objects if baseline["phase_roles"][name] != "hardware"
    ]
    quantitative_fractions = phase_subset_mass_fractions(
        project, histogram, phase_objects, quantitative_phase_names
    )
    refined_models_dir = output_dir / "refined_phase_models"
    refined_models_dir.mkdir()
    refined_models = {}
    for name, phase in phase_objects.items():
        cif = refined_models_dir / f"{clean_component(name)}.cif"
        phase.export_CIF(str(cif))
        refined_models[name] = {"path": str(cif), "sha256": sha256_file(cif)}
    candidate = {
        "candidate": "controlled_cell_sensitivity",
        "anchor": baseline["anchor"],
        "phase_roles": baseline["phase_roles"],
        "initial_mass_fractions": baseline["initial_mass_fractions"],
        "phase_parameterization": {
            "fixed_hap_scale": {baseline["anchor"]: 1.0},
            "refined_hap_scales": [name for name in phase_objects if name != baseline["anchor"]],
            "refined_histogram_scale": True,
            "refined_sample_shift": position_parameter == "Shift",
            "refined_sample_position_parameter": position_parameter,
            "cells_locked_during_final_qpa": True,
            "cells_accepted_before_final_qpa": accepted_phases,
            "instrument_profile_locked": True,
            "pure_phase_broadening_transferred": baseline["phase_parameterization"].get(
                "pure_phase_broadening_transferred", False
            ),
        },
        "refinement_passes": {
            "scale_background_after_cells": scale_passes,
            "sample_shift_after_cells_locked": shift_passes,
        },
        "refined_mass_fractions": {
            name: {"value": float(value), "esd": float(esd)}
            for name, (value, esd) in final_fractions.items()
        },
        "sample_normalized_mass_fractions": sample_fractions,
        "quantitative_normalized_mass_fractions": quantitative_fractions,
        "refined_hap_scales": {
            name: float(phase.HAPvalue("Scale", targethistlist=[histogram]))
            for name, phase in phase_objects.items()
        },
        "refined_histogram_scale": float(histogram.data["Sample Parameters"]["Scale"][0]),
        "refined_sample_shift": (
            refined_sample_position if position_parameter == "Shift" else None
        ),
        "refined_sample_position": (
            {
                "parameter": position_parameter,
                "value": refined_sample_position,
            }
            if position_parameter is not None
            else None
        ),
        "metrics": covariance_metrics(project),
        "residual_audit": residual_peak_audit(histogram),
        "cell_sensitivity_steps": steps,
        "refined_phase_models": refined_models,
        "files": {
            "fit_gpx": str(final_gpx),
            "fit_lst": str(final_gpx.with_suffix(".lst")),
        },
    }
    candidate["assessment"] = assess_sample_normalization(
        candidate,
        assess_real_qpa_candidate(candidate, **assessment_settings),
    )
    candidate["status"] = candidate["assessment"]["status"]
    result = {
        "status": candidate["status"],
        "candidate": candidate,
        "eligible_phases": [name for name, _ in eligible],
        "accepted_phases": accepted_phases,
        "steps": steps,
        "refined_phase_models": refined_models,
    }
    write_json_atomic(output_dir / "cell_sensitivity_summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--phase", action="append", required=True, help="NAME=/path/to/phase.cif")
    parser.add_argument(
        "--phase-role",
        action="append",
        default=[],
        help="NAME=sample|hardware|internal_standard; undeclared roles default to sample",
    )
    parser.add_argument("--pure-reference", action="append", default=[], help="NAME=/path/to/pure-pattern")
    parser.add_argument("--initial-composition", action="append", default=[], help="comma-separated fractions in --phase order")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--pattern-format", default="Topas xye")
    parser.add_argument("--background-order", type=int, default=10)
    parser.add_argument("--two-theta-min", type=float, default=10.0)
    parser.add_argument("--two-theta-max", type=float, default=145.0)
    parser.add_argument("--max-refinement-passes", type=int, default=12)
    parser.add_argument("--max-shift-over-esd", type=float, default=0.01)
    parser.add_argument("--correlation-limit", type=float, default=0.95)
    parser.add_argument("--path-spread-limit", type=float, default=0.002)
    parser.add_argument(
        "--phase-set-status",
        choices=("verified", "provisional", "unknown"),
        required=True,
        help="verified requires recorded evidence; provisional forces review; unknown blocks QPA",
    )
    parser.add_argument(
        "--phase-set-evidence",
        help="DOI or concise identifier for the declared phase-set evidence",
    )
    parser.add_argument(
        "--phase-set-evidence-kind",
        choices=("doi", "report", "frozen_phase_list"),
        help="evidence kind; required when --phase-set-status is verified",
    )
    parser.add_argument(
        "--phase-set-evidence-file",
        help="report or frozen phase-list file to hash into the protocol",
    )
    parser.add_argument(
        "--competitive-gof-tolerance",
        type=float,
        default=1e-5,
        help=(
            "Relative GOF window used to treat numerically equivalent anchor paths "
            "as competitive before ranking them by conditioning"
        ),
    )
    parser.add_argument(
        "--broadening-policy",
        choices=("locked", "transfer", "ensemble"),
        default="ensemble",
        help=(
            "locked is the primary conservative QPA model; transfer applies pure-phase "
            "Size/Mustrain values; ensemble reports locked as primary and transfer as a "
            "systematic-sensitivity model"
        ),
    )
    parser.add_argument(
        "--broadening-spread-review-limit",
        type=float,
        default=0.005,
        help="Review threshold for locked-versus-transferred phase-fraction spread",
    )
    parser.add_argument(
        "--preferred-orientation-policy",
        choices=("not_assessed", "assessed_negligible", "sensitivity"),
        default="not_assessed",
        help=(
            "not_assessed forces review; assessed_negligible requires evidence; "
            "sensitivity runs one-phase-at-a-time March-Dollase trials without auto-promotion"
        ),
    )
    parser.add_argument(
        "--preferred-orientation-axis",
        action="append",
        default=[],
        help="NAME=h,k,l axis for a one-phase March-Dollase sensitivity trial",
    )
    parser.add_argument("--preferred-orientation-evidence-file")
    parser.add_argument(
        "--preferred-orientation-minimum-relative-gof-improvement",
        type=float,
        default=0.005,
    )
    parser.add_argument(
        "--preferred-orientation-fraction-spread-review-limit",
        type=float,
        default=0.01,
    )
    parser.add_argument("--preferred-orientation-ratio-min", type=float, default=0.2)
    parser.add_argument("--preferred-orientation-ratio-max", type=float, default=5.0)
    parser.add_argument(
        "--microabsorption-policy",
        choices=("not_assessed", "assessed_negligible", "sensitivity"),
        default="not_assessed",
        help=(
            "sensitivity applies externally justified true-mass multiplier intervals; "
            "it does not alter the primary GSAS-II refinement"
        ),
    )
    parser.add_argument(
        "--microabsorption-multiplier",
        action="append",
        default=[],
        help="NAME=LOW,HIGH true-mass multiplier interval for every non-hardware phase",
    )
    parser.add_argument("--microabsorption-evidence-file")
    parser.add_argument(
        "--microabsorption-fraction-spread-review-limit",
        type=float,
        default=0.01,
    )
    parser.add_argument(
        "--internal-standard-added-fraction",
        type=float,
        help="known standard mass divided by total mass after addition",
    )
    parser.add_argument("--internal-standard-added-fraction-esd", type=float)
    parser.add_argument("--internal-standard-evidence-file")
    parser.add_argument("--trace-phase-threshold", type=float, default=0.05)
    parser.add_argument("--trace-detection-sigma", type=float, default=3.0)
    parser.add_argument("--trace-quantification-sigma", type=float, default=10.0)
    parser.add_argument(
        "--cell-policy",
        choices=("locked", "sensitivity", "controlled"),
        default="controlled",
        help="controlled may promote a safe abundant-phase cell branch; sensitivity never promotes it",
    )
    parser.add_argument("--cell-minimum-fraction", type=float, default=0.05)
    parser.add_argument("--cell-maximum-phases", type=int, default=3)
    parser.add_argument("--cell-volume-change-limit", type=float, default=0.03)
    parser.add_argument(
        "--cell-minimum-relative-gof-improvement", type=float, default=0.001
    )
    parser.add_argument("--repeatability-runs", type=int, default=3)
    parser.add_argument("--repeatability-start-floor", type=float, default=0.0001)
    parser.add_argument(
        "--repeatability-fraction-range-limit",
        type=float,
        default=0.005,
        help="maximum accepted per-phase range as a fraction; 0.005 equals 0.5 wt%%",
    )
    parser.add_argument("--phase-model-mass-relative-tolerance", type=float, default=0.005)
    parser.add_argument("--instrument-profile-status", choices=("calibrated", "uncalibrated"), required=True)
    parser.add_argument(
        "--instrument-calibration-summary",
        help="calibration_summary.json whose profile hash must match --instrument",
    )
    parser.add_argument("--held-out-id")
    parser.add_argument("--answer-status", choices=("blinded", "not_applicable"), default="not_applicable")
    parser.add_argument("--gsasii-path")
    args = parser.parse_args()
    if not 2 <= args.background_order <= 20:
        parser.error("--background-order must be between 2 and 20")
    if args.two_theta_min >= args.two_theta_max:
        parser.error("two-theta minimum must be less than maximum")
    if not 0.0 < args.correlation_limit < 1.0:
        parser.error("--correlation-limit must be between 0 and 1")
    if min(
        args.max_refinement_passes,
        args.max_shift_over_esd,
        args.path_spread_limit,
        args.competitive_gof_tolerance,
        args.broadening_spread_review_limit,
        args.preferred_orientation_fraction_spread_review_limit,
        args.preferred_orientation_ratio_min,
        args.preferred_orientation_ratio_max,
        args.microabsorption_fraction_spread_review_limit,
        args.trace_phase_threshold,
        args.trace_detection_sigma,
        args.trace_quantification_sigma,
        args.cell_minimum_fraction,
        args.cell_volume_change_limit,
        args.repeatability_fraction_range_limit,
        args.repeatability_start_floor,
    ) <= 0:
        parser.error("pass and numerical limits must be positive")
    if args.cell_minimum_fraction >= 1.0:
        parser.error("--cell-minimum-fraction must be below 1")
    if args.preferred_orientation_ratio_min >= args.preferred_orientation_ratio_max:
        parser.error("preferred-orientation ratio minimum must be below maximum")
    if args.preferred_orientation_minimum_relative_gof_improvement < 0.0:
        parser.error("preferred-orientation GOF improvement must be nonnegative")
    if args.trace_phase_threshold >= 1.0:
        parser.error("--trace-phase-threshold must be below 1")
    if args.trace_detection_sigma >= args.trace_quantification_sigma:
        parser.error("trace sigma thresholds require detection < quantification")
    if args.cell_maximum_phases < 1:
        parser.error("--cell-maximum-phases must be positive")
    if args.cell_minimum_relative_gof_improvement < 0.0:
        parser.error("--cell-minimum-relative-gof-improvement must be nonnegative")
    if args.repeatability_runs < 2:
        parser.error("--repeatability-runs must be at least 2")
    if args.phase_model_mass_relative_tolerance < 0.0:
        parser.error("--phase-model-mass-relative-tolerance must be nonnegative")
    if args.held_out_id and args.answer_status != "blinded":
        parser.error("held-out runs must declare --answer-status blinded")

    pattern = required_file(args.pattern, "mixture pattern")
    instrument = required_file(args.instrument, "instrument profile")
    calibration_summary_path = (
        required_file(args.instrument_calibration_summary, "instrument calibration summary")
        if args.instrument_calibration_summary
        else None
    )
    calibration_record = None
    if args.instrument_profile_status == "calibrated":
        if calibration_summary_path is None:
            parser.error(
                "calibrated profiles require --instrument-calibration-summary"
            )
        try:
            calibration_record = calibration_record_from_summary(
                calibration_summary_path, instrument
            )
        except ValueError as exc:
            parser.error(str(exc))
    elif calibration_summary_path is not None:
        parser.error(
            "--instrument-calibration-summary requires --instrument-profile-status calibrated"
        )
    phases = parse_named_files(args.phase, "phase")
    if len(phases) < 2:
        parser.error("multiphase QPA requires at least two declared phases")
    phase_names = list(phases)
    phase_roles = parse_phase_roles(args.phase_role, phase_names)
    sample_phase_names = [
        name for name in phase_names if phase_roles[name] == "sample"
    ]
    quantitative_phase_names = [
        name for name in phase_names if phase_roles[name] != "hardware"
    ]
    internal_standard_names = [
        name for name in phase_names if phase_roles[name] == "internal_standard"
    ]
    try:
        preferred_orientation_axes = parse_named_axes(
            args.preferred_orientation_axis, phase_names
        )
        microabsorption_intervals = parse_named_intervals(
            args.microabsorption_multiplier,
            quantitative_phase_names,
            "microabsorption multiplier",
        )
    except ValueError as exc:
        parser.error(str(exc))
    if any(name not in sample_phase_names for name in preferred_orientation_axes):
        parser.error("preferred-orientation sensitivity is allowed only for role=sample phases")
    preferred_orientation_evidence = (
        required_file(
            args.preferred_orientation_evidence_file,
            "preferred-orientation evidence",
        )
        if args.preferred_orientation_evidence_file
        else None
    )
    microabsorption_evidence = (
        required_file(args.microabsorption_evidence_file, "microabsorption evidence")
        if args.microabsorption_evidence_file
        else None
    )
    internal_standard_evidence = (
        required_file(args.internal_standard_evidence_file, "internal-standard evidence")
        if args.internal_standard_evidence_file
        else None
    )
    if args.preferred_orientation_policy == "sensitivity":
        if not preferred_orientation_axes:
            parser.error("preferred-orientation sensitivity requires at least one --preferred-orientation-axis")
        if preferred_orientation_evidence is None:
            parser.error("preferred-orientation sensitivity requires --preferred-orientation-evidence-file")
    elif preferred_orientation_axes:
        parser.error("--preferred-orientation-axis requires --preferred-orientation-policy sensitivity")
    if args.preferred_orientation_policy == "assessed_negligible" and preferred_orientation_evidence is None:
        parser.error("assessed-negligible preferred orientation requires an evidence file")
    if args.preferred_orientation_policy == "not_assessed" and preferred_orientation_evidence is not None:
        parser.error("preferred-orientation evidence requires an assessed policy")
    if args.microabsorption_policy == "sensitivity":
        if set(microabsorption_intervals) != set(quantitative_phase_names):
            parser.error("microabsorption sensitivity requires one multiplier interval for every non-hardware phase")
        if microabsorption_evidence is None:
            parser.error("microabsorption sensitivity requires --microabsorption-evidence-file")
    elif microabsorption_intervals:
        parser.error("--microabsorption-multiplier requires --microabsorption-policy sensitivity")
    if args.microabsorption_policy == "assessed_negligible" and microabsorption_evidence is None:
        parser.error("assessed-negligible microabsorption requires an evidence file")
    if args.microabsorption_policy == "not_assessed" and microabsorption_evidence is not None:
        parser.error("microabsorption evidence requires an assessed policy")
    if args.internal_standard_added_fraction is not None:
        if len(internal_standard_names) != 1:
            parser.error("amorphous quantification requires exactly one role=internal_standard phase")
        if not 0.0 < args.internal_standard_added_fraction < 1.0:
            parser.error("--internal-standard-added-fraction must be between zero and one")
        if internal_standard_evidence is None:
            parser.error("amorphous quantification requires --internal-standard-evidence-file")
    elif args.internal_standard_added_fraction_esd is not None:
        parser.error("internal-standard fraction ESD requires the added fraction")
    if args.internal_standard_added_fraction_esd is not None and args.internal_standard_added_fraction_esd <= 0.0:
        parser.error("internal-standard fraction ESD must be positive")
    if internal_standard_evidence is not None and args.internal_standard_added_fraction is None:
        parser.error("internal-standard evidence requires the known added fraction")
    try:
        preferred_orientation_contract = (
            validate_common_problem_evidence(
                load_evidence_json(
                    preferred_orientation_evidence,
                    "preferred-orientation evidence",
                ),
                kind="preferred_orientation_assessment",
                policy=args.preferred_orientation_policy,
                expected_phase_axes=preferred_orientation_axes,
            )
            if preferred_orientation_evidence
            else None
        )
        microabsorption_contract = (
            validate_common_problem_evidence(
                load_evidence_json(
                    microabsorption_evidence,
                    "microabsorption evidence",
                ),
                kind="microabsorption_assessment",
                policy=args.microabsorption_policy,
                expected_phase_intervals=microabsorption_intervals,
            )
            if microabsorption_evidence
            else None
        )
        internal_standard_contract = (
            validate_common_problem_evidence(
                load_evidence_json(
                    internal_standard_evidence,
                    "internal-standard evidence",
                ),
                kind="internal_standard_addition",
                expected_standard_phase=internal_standard_names[0],
                expected_added_fraction=args.internal_standard_added_fraction,
                expected_added_fraction_esd=args.internal_standard_added_fraction_esd,
            )
            if internal_standard_evidence
            else None
        )
    except ValueError as exc:
        parser.error(str(exc))
    pure_references = parse_named_files(args.pure_reference, "pure reference")
    unknown_pure = sorted(set(pure_references) - set(phases))
    if unknown_pure:
        parser.error(f"pure references have undeclared phases: {', '.join(unknown_pure)}")
    if args.repeatability_start_floor >= 1.0 / len(phase_names):
        parser.error("--repeatability-start-floor is too large for the phase count")
    starts = starting_compositions(phase_names, args.initial_composition)
    phase_set_evidence_file = (
        required_file(args.phase_set_evidence_file, "phase-set evidence")
        if args.phase_set_evidence_file
        else None
    )
    if args.phase_set_status == "verified":
        if args.phase_set_evidence_kind is None:
            parser.error("verified phase sets require --phase-set-evidence-kind")
        if args.phase_set_evidence_kind == "doi":
            if not args.phase_set_evidence or not re.match(
                r"^10\.\d{4,9}/\S+$", args.phase_set_evidence.strip()
            ):
                parser.error("DOI evidence must use a 10.xxxx/... identifier")
        elif phase_set_evidence_file is None:
            parser.error("report/frozen phase-set evidence requires --phase-set-evidence-file")
    evidence_record = {
        "kind": args.phase_set_evidence_kind,
        "identifier": args.phase_set_evidence,
        "file": (
            {
                "path": str(phase_set_evidence_file),
                "sha256": sha256_file(phase_set_evidence_file),
            }
            if phase_set_evidence_file
            else None
        ),
    }
    frozen_evidence = None
    if args.phase_set_evidence_kind == "doi" and args.phase_set_evidence:
        frozen_evidence = f"doi:{args.phase_set_evidence.strip()}"
    elif phase_set_evidence_file:
        frozen_evidence = (
            f"{args.phase_set_evidence_kind or 'file'}:"
            f"sha256:{sha256_file(phase_set_evidence_file)}"
        )
    elif args.phase_set_evidence:
        frozen_evidence = f"identifier:{args.phase_set_evidence.strip()}"
    phase_set_gate = phase_set_completeness_gate(
        args.phase_set_status,
        evidence=frozen_evidence,
        held_out=bool(args.held_out_id),
    )
    common_problem_evidence = {
        "preferred_orientation": (
            {
                "path": str(preferred_orientation_evidence),
                "sha256": sha256_file(preferred_orientation_evidence),
                "validated_contract": preferred_orientation_contract,
            }
            if preferred_orientation_evidence
            else None
        ),
        "microabsorption": (
            {
                "path": str(microabsorption_evidence),
                "sha256": sha256_file(microabsorption_evidence),
                "validated_contract": microabsorption_contract,
            }
            if microabsorption_evidence
            else None
        ),
        "internal_standard_addition": (
            {
                "path": str(internal_standard_evidence),
                "sha256": sha256_file(internal_standard_evidence),
                "validated_contract": internal_standard_contract,
            }
            if internal_standard_evidence
            else None
        ),
    }
    gsasii_path = resolve_gsasii_path(args.gsasii_path)
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    (output_dir / "pure_phase_priors").mkdir()
    (output_dir / "candidates").mkdir()
    limits = (args.two_theta_min, args.two_theta_max)
    assessment_settings = {
        "correlation_limit": args.correlation_limit,
        "shift_limit": args.max_shift_over_esd,
        "residual_minimum_sigma": 6.0,
        "residual_minimum_pattern_percent": 2.0,
        "residual_hard_pattern_percent": 10.0,
        "residual_modeled_peak_minimum_percent": 1.0,
    }
    script_path = Path(__file__).resolve()
    core_path = script_path.with_name("qpa_core.py")
    common_problems_path = script_path.with_name("qpa_common_problems.py")
    audit_script_path = script_path.with_name("audit_phase_models.py")
    protocol = {
        "schema_version": 4,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sample_id": args.sample_id,
        "held_out_id": args.held_out_id,
        "answer_status_at_freeze": args.answer_status,
        "answer_values_present": False,
        "inputs": {
            "pattern": {"path": str(pattern), "sha256": sha256_file(pattern)},
            "instrument": {"path": str(instrument), "sha256": sha256_file(instrument)},
            "instrument_calibration": calibration_record,
            "phases": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in phases.items()},
            "pure_references": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in pure_references.items()},
            "phase_set_evidence": evidence_record,
            "common_problem_evidence": common_problem_evidence,
        },
        "code": {
            "driver": {"path": str(script_path), "sha256": sha256_file(script_path)},
            "qpa_core": {"path": str(core_path), "sha256": sha256_file(core_path)},
            "qpa_common_problems": {
                "path": str(common_problems_path),
                "sha256": sha256_file(common_problems_path),
            },
            "phase_model_auditor": {
                "path": str(audit_script_path),
                "sha256": sha256_file(audit_script_path),
            },
        },
        "settings": {
            "background_order": args.background_order,
            "limits": limits,
            "max_refinement_passes": args.max_refinement_passes,
            "max_shift_over_esd": args.max_shift_over_esd,
            "correlation_limit": args.correlation_limit,
            "path_spread_limit": args.path_spread_limit,
            "competitive_gof_tolerance": args.competitive_gof_tolerance,
            "broadening_policy": args.broadening_policy,
            "broadening_spread_review_limit": args.broadening_spread_review_limit,
            "preferred_orientation_policy": args.preferred_orientation_policy,
            "preferred_orientation_axes": {
                name: list(axis) for name, axis in preferred_orientation_axes.items()
            },
            "preferred_orientation_minimum_relative_gof_improvement": args.preferred_orientation_minimum_relative_gof_improvement,
            "preferred_orientation_fraction_spread_review_limit": args.preferred_orientation_fraction_spread_review_limit,
            "preferred_orientation_ratio_bounds": [
                args.preferred_orientation_ratio_min,
                args.preferred_orientation_ratio_max,
            ],
            "microabsorption_policy": args.microabsorption_policy,
            "microabsorption_multiplier_intervals": {
                name: list(interval) for name, interval in microabsorption_intervals.items()
            },
            "microabsorption_fraction_spread_review_limit": args.microabsorption_fraction_spread_review_limit,
            "internal_standard_added_fraction": args.internal_standard_added_fraction,
            "internal_standard_added_fraction_esd": args.internal_standard_added_fraction_esd,
            "trace_phase_threshold": args.trace_phase_threshold,
            "trace_detection_sigma": args.trace_detection_sigma,
            "trace_quantification_sigma": args.trace_quantification_sigma,
            "common_problem_evidence": common_problem_evidence,
            "instrument_profile_status": args.instrument_profile_status,
            "phase_set_gate": phase_set_gate,
            "phase_set_evidence": evidence_record,
            "phase_roles": phase_roles,
            "internal_standard_present": any(
                role == "internal_standard" for role in phase_roles.values()
            ),
            "instrument_calibration": calibration_record,
            "starting_compositions": starts,
            "anchors": sample_phase_names,
            "cell_policy": args.cell_policy,
            "cell_minimum_fraction": args.cell_minimum_fraction,
            "cell_maximum_phases": args.cell_maximum_phases,
            "cell_volume_change_limit": args.cell_volume_change_limit,
            "cell_minimum_relative_gof_improvement": args.cell_minimum_relative_gof_improvement,
            "repeatability_runs": args.repeatability_runs,
            "repeatability_start_floor": args.repeatability_start_floor,
            "repeatability_fraction_range_limit": args.repeatability_fraction_range_limit,
            "phase_model_mass_relative_tolerance": args.phase_model_mass_relative_tolerance,
            "instrument_profile": "locked",
            "sample_position": "refine Shift, else DisplaceX/DisplaceY, after scale/background when available",
            "pure_prior_policy": "compare locked, Size, Mustrain and diagnostic both; refine positive physical Uiso on the pure pattern; use that structure in the mixture",
            "broadening_policy_note": "ensemble keeps calibrated instrument broadening as the primary composition model and uses transferred pure-phase broadening only to estimate systematic model sensitivity",
        },
    }
    write_json_atomic(output_dir / "protocol_manifest.json", protocol)

    sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIElem as G2elem
    from GSASII import GSASIIscriptable as G2sc
    from GSASII import GSASIIpath
    from GSASII import GSASIIspc as G2spc

    G2sc.SetPrintLevel("warn")
    phase_model_audit = audit_phase_models(
        G2sc=G2sc,
        G2elem=G2elem,
        G2spc=G2spc,
        phases=phases,
        mass_relative_tolerance=args.phase_model_mass_relative_tolerance,
    )
    write_json_atomic(output_dir / "phase_model_audit.json", phase_model_audit)
    preflight_hard_failures = list(phase_set_gate["hard_failures"]) + list(
        phase_model_audit["hard_failures"]
    )
    preflight_review_flags = list(phase_set_gate["review_flags"]) + list(
        phase_model_audit["review_flags"]
    )
    if args.preferred_orientation_policy == "not_assessed":
        preflight_review_flags.append("preferred_orientation_not_assessed")
    if args.microabsorption_policy == "not_assessed":
        preflight_review_flags.append("microabsorption_not_assessed")
    if internal_standard_names and args.internal_standard_added_fraction is None:
        preflight_review_flags.append("internal_standard_addition_not_quantified")
    if preflight_hard_failures:
        status = "fail"
        summary = {
            "schema_version": 4,
            "sample_id": args.sample_id,
            "route": "multiphase_qpa",
            "status": status,
            "real_gsasii": True,
            "gsasii": {
                "path": str(gsasii_path),
                "version_number": GSASIIpath.GetVersionNumber(),
                "python": sys.version,
            },
            "protocol_manifest": str(output_dir / "protocol_manifest.json"),
            "phase_set_gate": phase_set_gate,
            "phase_set_evidence": evidence_record,
            "phase_roles": phase_roles,
            "internal_standard_present": any(
                role == "internal_standard" for role in phase_roles.values()
            ),
            "instrument_calibration": calibration_record,
            "common_problem_evidence": common_problem_evidence,
            "phase_model_audit": str(output_dir / "phase_model_audit.json"),
            "scientific_assessment": {
                "status": status,
                "hard_failures": list(dict.fromkeys(preflight_hard_failures)),
                "review_flags": list(dict.fromkeys(preflight_review_flags)),
            },
            "primary_candidate_count": 0,
            "limitations": [
                "numerical refinement was blocked by the model preflight",
                "no figure generated",
            ],
        }
        write_json_atomic(output_dir / "qpa_summary.json", summary)
        prediction = {
            "schema_version": 4,
            "sample_id": args.sample_id,
            "held_out_id": args.held_out_id,
            "prediction_archived_at": datetime.now(timezone.utc).isoformat(),
            "answer_status": args.answer_status,
            "selected_candidate": None,
            "status": status,
            "mass_fractions": None,
            "all_modeled_crystalline_scale_fractions": None,
            "phase_roles": phase_roles,
            "internal_standard_present": any(
                role == "internal_standard" for role in phase_roles.values()
            ),
            "metrics": None,
            "reported_uncertainties": {},
            "protocol_manifest_sha256": sha256_file(output_dir / "protocol_manifest.json"),
            "qpa_summary_sha256_before_prediction_archive": sha256_file(output_dir / "qpa_summary.json"),
        }
        write_json_atomic(output_dir / "prediction_archive.json", prediction)
        print(json.dumps(json_clean({"status": status, "selected": None, "summary": str(output_dir / "qpa_summary.json"), "prediction_archive": str(output_dir / "prediction_archive.json")}), indent=2))
        return 2

    pure_priors = {}
    for name in phase_names:
        if name not in pure_references:
            continue
        pure_priors[name] = run_pure_prior(
            G2sc=G2sc,
            phase_name=name,
            pure_pattern=pure_references[name],
            cif=phases[name],
            instrument=instrument,
            output_dir=output_dir / "pure_phase_priors" / clean_component(name),
            pattern_format=args.pattern_format,
            background_order=args.background_order,
            limits=limits,
            max_passes=args.max_refinement_passes,
            shift_limit=args.max_shift_over_esd,
            correlation_limit=args.correlation_limit,
        )

    mixture_phases = dict(phases)
    for name, prior in pure_priors.items():
        refined = prior.get("refined_structure_cif")
        if refined:
            mixture_phases[name] = Path(refined["path"])

    candidates = []
    primary_transfer = args.broadening_policy == "transfer"
    for anchor in sample_phase_names:
        for start_index, initial in enumerate(starts, start=1):
            destination = output_dir / "candidates" / f"anchor_{clean_component(anchor)}_start_{start_index:02d}"
            candidates.append(
                run_mixture_candidate(
                    G2sc=G2sc,
                    pattern=pattern,
                    instrument=instrument,
                    phases=mixture_phases,
                    phase_roles=phase_roles,
                    pure_priors=pure_priors,
                    transfer_broadening=primary_transfer,
                    anchor=anchor,
                    initial=initial,
                    initial_index=start_index,
                    candidate_label=None,
                    output_dir=destination,
                    pattern_format=args.pattern_format,
                    background_order=args.background_order,
                    limits=limits,
                    max_passes=args.max_refinement_passes,
                    shift_limit=args.max_shift_over_esd,
                    assessment_settings=assessment_settings,
                )
            )
            write_json_atomic(output_dir / "candidate_summary.json", {"candidates": candidates})

    selectable = [candidate for candidate in candidates if "metrics" in candidate]
    spreads: dict[str, float | None] = {}
    sensitivity = None
    preferred_orientation_trials: list[dict[str, Any]] = []
    preferred_orientation_assessment = None
    preferred_orientation_model_spread: dict[str, float | None] = {
        name: None for name in sample_phase_names
    }
    microabsorption_assessment = None
    microabsorption_model_spread: dict[str, float | None] = {
        name: None for name in sample_phase_names
    }
    amorphous_content = None
    trace_phase_assessment = None
    cell_sensitivity = None
    cell_model_spread: dict[str, float | None] = {
        name: None for name in sample_phase_names
    }
    broadening_model_spread: dict[str, float | None] = {
        name: None for name in sample_phase_names
    }
    repeatability = None
    repeatability_records: list[dict[str, Any]] = []
    reported_uncertainties: dict[str, dict[str, float | None]] = {}
    final_model_phases = dict(mixture_phases)
    model_competitive: list[dict[str, Any]] = []
    if not selectable:
        selected = None
        competitive = []
        status = "fail"
        hard_failures = list(preflight_hard_failures) + ["no_selectable_candidate"]
        review_flags = list(preflight_review_flags)
    else:
        locked_selected, competitive = select_competitive_candidate(
            selectable, relative_gof_tolerance=args.competitive_gof_tolerance
        )
        for name in sample_phase_names:
            values = [
                float(candidate["sample_normalized_mass_fractions"][name]["value"])
                for candidate in competitive
            ]
            spreads[name] = max(values) - min(values) if values else None
        selected = locked_selected
        if args.cell_policy != "locked":
            cell_sensitivity = run_controlled_cell_sensitivity(
                G2sc=G2sc,
                baseline=locked_selected,
                output_dir=output_dir / "sensitivity" / "controlled_cells",
                background_order=args.background_order,
                limits=limits,
                max_passes=args.max_refinement_passes,
                shift_limit=args.max_shift_over_esd,
                correlation_limit=args.correlation_limit,
                minimum_fraction=args.cell_minimum_fraction,
                maximum_phases=args.cell_maximum_phases,
                volume_change_limit=args.cell_volume_change_limit,
                minimum_relative_gof_improvement=args.cell_minimum_relative_gof_improvement,
                assessment_settings=assessment_settings,
            )
            cell_candidate = cell_sensitivity.get("candidate")
            if cell_candidate:
                for name in sample_phase_names:
                    cell_model_spread[name] = abs(
                        float(locked_selected["sample_normalized_mass_fractions"][name]["value"])
                        - float(cell_candidate["sample_normalized_mass_fractions"][name]["value"])
                    )
                if args.cell_policy == "controlled":
                    selected, model_competitive = select_competitive_candidate(
                        [locked_selected, cell_candidate],
                        relative_gof_tolerance=args.competitive_gof_tolerance,
                    )
                    if selected["candidate"] == cell_candidate["candidate"]:
                        final_model_phases = {
                            name: Path(item["path"])
                            for name, item in cell_sensitivity["refined_phase_models"].items()
                        }

        hard_failures = list(preflight_hard_failures) + list(
            selected["assessment"]["hard_failures"]
        )
        review_flags = list(preflight_review_flags) + list(
            selected["assessment"]["review_flags"]
        )
        for name in sample_phase_names:
            if spreads[name] is not None and spreads[name] > args.path_spread_limit:
                review_flags.append(f"competitive_path_spread:{name}")
            if (
                cell_model_spread[name] is not None
                and cell_model_spread[name] > args.path_spread_limit
            ):
                review_flags.append(f"cell_model_fraction_spread:{name}")
        if args.cell_policy != "locked" and not cell_sensitivity.get("accepted_phases"):
            review_flags.append("controlled_cell_sensitivity_unavailable")
        if args.instrument_profile_status != "calibrated":
            review_flags.append("uncalibrated_instrument_profile")
        missing_priors = sorted(set(sample_phase_names) - set(pure_priors))
        if missing_priors and args.held_out_id:
            review_flags.append("missing_pure_phase_structure_priors:" + ",".join(missing_priors))
        failed_priors = sorted(name for name, prior in pure_priors.items() if prior["status"] != "pass")
        if failed_priors:
            review_flags.append("failed_pure_phase_priors:" + ",".join(failed_priors))
        if args.broadening_policy == "ensemble":
            transferable = all(
                pure_priors.get(name, {}).get("status") in {"pass", "review"}
                and pure_priors.get(name, {}).get("selected_model") in {"Size", "Mustrain"}
                for name in sample_phase_names
            )
            if transferable:
                sensitivity_dir = output_dir / "sensitivity" / "transferred_pure_broadening"
                sensitivity = run_mixture_candidate(
                    G2sc=G2sc,
                    pattern=pattern,
                    instrument=instrument,
                    phases=final_model_phases,
                    phase_roles=phase_roles,
                    pure_priors=pure_priors,
                    transfer_broadening=True,
                    anchor=selected["anchor"],
                    initial=selected["initial_mass_fractions"],
                    initial_index=0,
                    candidate_label="sensitivity_transferred_pure_broadening",
                    output_dir=sensitivity_dir,
                    pattern_format=args.pattern_format,
                    background_order=args.background_order,
                    limits=limits,
                    max_passes=args.max_refinement_passes,
                    shift_limit=args.max_shift_over_esd,
                    assessment_settings=assessment_settings,
                    initial_hap_scales=selected["refined_hap_scales"],
                    initial_histogram_scale=selected["refined_histogram_scale"],
                    initial_sample_shift=candidate_sample_position_value(selected),
                )
                if "sample_normalized_mass_fractions" in sensitivity:
                    for name in sample_phase_names:
                        broadening_model_spread[name] = abs(
                            float(selected["sample_normalized_mass_fractions"][name]["value"])
                            - float(sensitivity["sample_normalized_mass_fractions"][name]["value"])
                        )
                    if max(float(value) for value in broadening_model_spread.values() if value is not None) > args.broadening_spread_review_limit:
                        review_flags.append("broadening_model_fraction_spread_exceeds_limit")
                else:
                    review_flags.append("broadening_sensitivity_model_failed")
            else:
                review_flags.append("broadening_sensitivity_unavailable")

        if args.preferred_orientation_policy == "sensitivity":
            for phase_name, axis in preferred_orientation_axes.items():
                trial = run_mixture_candidate(
                    G2sc=G2sc,
                    pattern=pattern,
                    instrument=instrument,
                    phases=final_model_phases,
                    phase_roles=phase_roles,
                    pure_priors=pure_priors,
                    transfer_broadening=primary_transfer,
                    anchor=selected["anchor"],
                    initial=selected["initial_mass_fractions"],
                    initial_index=0,
                    candidate_label=(
                        f"sensitivity_march_dollase_{clean_component(phase_name)}"
                    ),
                    output_dir=(
                        output_dir
                        / "sensitivity"
                        / "preferred_orientation"
                        / clean_component(phase_name)
                    ),
                    pattern_format=args.pattern_format,
                    background_order=args.background_order,
                    limits=limits,
                    max_passes=args.max_refinement_passes,
                    shift_limit=args.max_shift_over_esd,
                    assessment_settings=assessment_settings,
                    initial_hap_scales=selected["refined_hap_scales"],
                    initial_histogram_scale=selected["refined_histogram_scale"],
                    initial_sample_shift=candidate_sample_position_value(selected),
                    preferred_orientation={"phase": phase_name, "axis": axis},
                )
                preferred_orientation_trials.append(trial)
            preferred_orientation_assessment = assess_preferred_orientation_sensitivity(
                selected,
                preferred_orientation_trials,
                correlation_limit=args.correlation_limit,
                shift_limit=args.max_shift_over_esd,
                minimum_relative_gof_improvement=args.preferred_orientation_minimum_relative_gof_improvement,
                fraction_spread_review_limit=args.preferred_orientation_fraction_spread_review_limit,
                ratio_bounds=(
                    args.preferred_orientation_ratio_min,
                    args.preferred_orientation_ratio_max,
                ),
            )
            review_flags.extend(preferred_orientation_assessment["review_flags"])
            if all(
                bool(item["numerically_safe"])
                for item in preferred_orientation_assessment["trials"]
            ):
                for name, spread in preferred_orientation_assessment[
                    "phase_fraction_spread"
                ].items():
                    preferred_orientation_model_spread[name] = float(spread)
        elif args.preferred_orientation_policy == "assessed_negligible":
            preferred_orientation_assessment = {
                "status": "pass",
                "hard_failures": [],
                "review_flags": [],
                "policy": "assessed_negligible",
                "evidence": common_problem_evidence["preferred_orientation"],
                "phase_fraction_spread": {
                    name: 0.0 for name in sample_phase_names
                },
            }
            preferred_orientation_model_spread = {
                name: 0.0 for name in sample_phase_names
            }

        repeatability_records = [selected]
        repeatability_start = restart_composition(
            selected, minimum_fraction=args.repeatability_start_floor
        )
        for repeat_index in range(2, args.repeatability_runs + 1):
            repeatability_records.append(
                run_mixture_candidate(
                    G2sc=G2sc,
                    pattern=pattern,
                    instrument=instrument,
                    phases=final_model_phases,
                    phase_roles=phase_roles,
                    pure_priors=pure_priors,
                    transfer_broadening=primary_transfer,
                    anchor=selected["anchor"],
                    initial=repeatability_start,
                    initial_index=repeat_index,
                    candidate_label=f"repeatability_{repeat_index:02d}",
                    output_dir=output_dir / "repeatability" / f"run_{repeat_index:02d}",
                    pattern_format=args.pattern_format,
                    background_order=args.background_order,
                    limits=limits,
                    max_passes=args.max_refinement_passes,
                    shift_limit=args.max_shift_over_esd,
                    assessment_settings=assessment_settings,
                    initial_hap_scales=selected["refined_hap_scales"],
                    initial_histogram_scale=selected["refined_histogram_scale"],
                    initial_sample_shift=candidate_sample_position_value(selected),
                )
            )
        repeatability = summarize_qpa_repeatability(
            repeatability_records,
            sample_phase_names,
            fraction_range_limit=args.repeatability_fraction_range_limit,
            fraction_key="sample_normalized_mass_fractions",
        )
        repeatability["restart_initial_mass_fractions"] = repeatability_start
        repeatability["restart_floor"] = args.repeatability_start_floor
        hard_failures.extend(repeatability["hard_failures"])
        if args.microabsorption_policy == "sensitivity":
            quantitative_microabsorption = microabsorption_multiplier_sensitivity(
                {
                    name: float(item["value"])
                    for name, item in selected[
                        "quantitative_normalized_mass_fractions"
                    ].items()
                },
                microabsorption_intervals,
                spread_review_limit=args.microabsorption_fraction_spread_review_limit,
            )
            sample_microabsorption = microabsorption_multiplier_sensitivity(
                {
                    name: float(item["value"])
                    for name, item in selected[
                        "sample_normalized_mass_fractions"
                    ].items()
                },
                {
                    name: microabsorption_intervals[name]
                    for name in sample_phase_names
                },
                spread_review_limit=args.microabsorption_fraction_spread_review_limit,
            )
            micro_review_flags = list(
                dict.fromkeys(
                    quantitative_microabsorption["review_flags"]
                    + sample_microabsorption["review_flags"]
                )
            )
            microabsorption_assessment = {
                "status": "review" if micro_review_flags else "pass",
                "hard_failures": [],
                "review_flags": micro_review_flags,
                "policy": "sensitivity",
                "evidence": common_problem_evidence["microabsorption"],
                "quantitative_crystalline_plus_standard": quantitative_microabsorption,
                "sample_normalized": sample_microabsorption,
            }
            review_flags.extend(micro_review_flags)
            for name in sample_phase_names:
                microabsorption_model_spread[name] = float(
                    sample_microabsorption["phases"][name]["maximum_absolute_shift"]
                )
        elif args.microabsorption_policy == "assessed_negligible":
            microabsorption_assessment = {
                "status": "pass",
                "hard_failures": [],
                "review_flags": [],
                "policy": "assessed_negligible",
                "evidence": common_problem_evidence["microabsorption"],
            }
            microabsorption_model_spread = {
                name: 0.0 for name in sample_phase_names
            }

        if args.internal_standard_added_fraction is not None:
            standard_name = internal_standard_names[0]
            standard_result = selected["quantitative_normalized_mass_fractions"][
                standard_name
            ]
            amorphous_content = amorphous_from_internal_standard(
                added_standard_fraction=args.internal_standard_added_fraction,
                refined_standard_fraction=float(standard_result["value"]),
                refined_standard_esd=standard_result.get("esd"),
                added_standard_fraction_esd=args.internal_standard_added_fraction_esd,
            )
            amorphous_content["internal_standard_phase"] = standard_name
            amorphous_content["evidence"] = common_problem_evidence[
                "internal_standard_addition"
            ]
            if microabsorption_assessment and args.microabsorption_policy == "sensitivity":
                standard_micro = microabsorption_assessment[
                    "quantitative_crystalline_plus_standard"
                ]["phases"][standard_name]
                amorphous_micro = amorphous_interval_from_internal_standard(
                    added_standard_fraction=args.internal_standard_added_fraction,
                    refined_standard_fraction_interval=(
                        float(standard_micro["minimum"]),
                        float(standard_micro["maximum"]),
                    ),
                )
                amorphous_content["microabsorption_sensitivity"] = amorphous_micro
                statistical_esd = amorphous_content.get("amorphous_fraction_esd")
                amorphous_content["conservative_combined_uncertainty"] = (
                    math.sqrt(
                        float(statistical_esd) ** 2
                        + float(amorphous_micro["half_range"]) ** 2
                    )
                    if statistical_esd is not None
                    else None
                )
                amorphous_content["review_flags"].extend(
                    amorphous_micro["review_flags"]
                )
            hard_failures.extend(amorphous_content["hard_failures"])
            review_flags.extend(amorphous_content["review_flags"])
        for name in sample_phase_names:
            statistical = float(
                selected["sample_normalized_mass_fractions"][name]["esd"]
            )
            broadening = broadening_model_spread[name]
            cell = cell_model_spread[name]
            preferred_orientation_spread = preferred_orientation_model_spread[name]
            microabsorption_spread = microabsorption_model_spread[name]
            repeat_range = repeatability["phases"][name]["range"]
            repeatability_half_range = (
                0.5 * float(repeat_range) if repeat_range is not None else None
            )
            available_components = [
                float(value)
                for value in (
                    broadening,
                    cell,
                    preferred_orientation_spread,
                    microabsorption_spread,
                    repeatability_half_range,
                )
                if value is not None
            ]
            available_combined = math.sqrt(
                statistical**2 + sum(value**2 for value in available_components)
            )
            model_components_complete = (
                (args.broadening_policy != "ensemble" or broadening is not None)
                and (args.cell_policy == "locked" or cell is not None)
                and (
                    args.preferred_orientation_policy != "sensitivity"
                    or preferred_orientation_spread is not None
                )
                and args.preferred_orientation_policy != "not_assessed"
                and (
                    args.microabsorption_policy != "sensitivity"
                    or microabsorption_spread is not None
                )
                and args.microabsorption_policy != "not_assessed"
                and repeatability["status"] == "pass"
            )
            reported_uncertainties[name] = {
                "statistical_esd": statistical,
                "broadening_model_spread": broadening,
                "cell_model_spread": cell,
                "preferred_orientation_model_spread": preferred_orientation_spread,
                "microabsorption_model_spread": microabsorption_spread,
                "repeatability_half_range": repeatability_half_range,
                "available_components_combined": available_combined,
                "model_components_complete": model_components_complete,
                "conservative_combined": available_combined if model_components_complete else None,
            }
        trace_phase_assessment = assess_trace_phases(
            selected["sample_normalized_mass_fractions"],
            reported_uncertainties,
            trace_fraction_threshold=args.trace_phase_threshold,
            detection_sigma=args.trace_detection_sigma,
            quantification_sigma=args.trace_quantification_sigma,
        )
        review_flags.extend(trace_phase_assessment["review_flags"])
        status = "fail" if hard_failures else "review" if review_flags else "pass"
        selected_dir = output_dir / "selected"
        selected_dir.mkdir()
        selected_source = Path(selected["files"]["fit_gpx"])
        selected_gpx = selected_dir / f"{clean_component(args.sample_id)}_qpa.gpx"
        shutil.copy2(selected_source, selected_gpx)
        selected_lst_source = selected_source.with_suffix(".lst")
        if selected_lst_source.is_file():
            shutil.copy2(selected_lst_source, selected_gpx.with_suffix(".lst"))

    write_json_atomic(
        output_dir / "candidate_summary.json",
        {
            "schema_version": 4,
            "primary_candidates": candidates,
            "competitive_candidates": [candidate["candidate"] for candidate in competitive],
            "model_competitive_candidates": [
                candidate["candidate"] for candidate in model_competitive
            ],
            "cell_sensitivity": cell_sensitivity,
            "preferred_orientation_trials": preferred_orientation_trials,
            "repeatability_records": repeatability_records,
            "selected_candidate": selected["candidate"] if selected else None,
        },
    )
    summary = {
        "schema_version": 4,
        "sample_id": args.sample_id,
        "route": "multiphase_qpa",
        "status": status,
        "real_gsasii": True,
        "gsasii": {
            "path": str(gsasii_path),
            "version_number": GSASIIpath.GetVersionNumber(),
            "python": sys.version,
        },
        "protocol_manifest": str(output_dir / "protocol_manifest.json"),
        "phase_set_gate": phase_set_gate,
        "phase_set_evidence": evidence_record,
        "phase_roles": phase_roles,
        "internal_standard_present": any(
            role == "internal_standard" for role in phase_roles.values()
        ),
        "instrument_calibration": calibration_record,
        "common_problem_evidence": common_problem_evidence,
        "phase_model_audit": {
            "path": str(output_dir / "phase_model_audit.json"),
            "sha256": sha256_file(output_dir / "phase_model_audit.json"),
            "status": phase_model_audit["status"],
        },
        "pure_phase_priors": pure_priors,
        "mixture_phase_models": {
            name: {"path": str(path), "sha256": sha256_file(path)}
            for name, path in mixture_phases.items()
        },
        "broadening_policy": args.broadening_policy,
        "cell_policy": args.cell_policy,
        "selected_candidate": selected["candidate"] if selected else None,
        "competitive_candidates": [candidate["candidate"] for candidate in competitive],
        "model_competitive_candidates": [
            candidate["candidate"] for candidate in model_competitive
        ],
        "competitive_path_spread": spreads,
        "selected_result": selected,
        "cell_sensitivity": cell_sensitivity,
        "cell_model_fraction_spread": cell_model_spread,
        "broadening_sensitivity": sensitivity,
        "broadening_model_fraction_spread": broadening_model_spread,
        "preferred_orientation_policy": args.preferred_orientation_policy,
        "preferred_orientation_sensitivity": preferred_orientation_assessment,
        "preferred_orientation_model_fraction_spread": preferred_orientation_model_spread,
        "microabsorption_policy": args.microabsorption_policy,
        "microabsorption_sensitivity": microabsorption_assessment,
        "microabsorption_model_fraction_spread": microabsorption_model_spread,
        "amorphous_content": amorphous_content,
        "trace_phase_assessment": trace_phase_assessment,
        "repeatability": repeatability,
        "reported_uncertainties": reported_uncertainties,
        "scientific_assessment": {
            "status": status,
            "hard_failures": list(dict.fromkeys(hard_failures)),
            "review_flags": list(dict.fromkeys(review_flags)),
        },
        "primary_candidate_count": len(candidates),
        "sensitivity_candidate_count": (
            int(sensitivity is not None)
            + int(cell_sensitivity is not None)
            + len(preferred_orientation_trials)
            + int(microabsorption_assessment is not None)
        ),
        "repeatability_run_count": len(repeatability_records),
        "candidates": candidates,
        "limitations": [
            "declared phase set only; unknown or unverified phase sets are blocked or capped at review rather than silently treated as complete",
            "abundant-phase cells may be tested one at a time, but every accepted cell is locked again before final QPA",
            "the instrument profile remains locked; an uncalibrated profile forces review",
            "pure-reference Uiso refinement is used only when convergence, SVD, correlation and physical-value gates pass",
            "pure-phase Size/Mustrain transfer is not selected by lower Rwp in ensemble mode; its fraction difference is reported as systematic sensitivity",
            "reported QPA is normalized only across role=sample phases; hardware and internal-standard phases remain modeled but are excluded from sample normalization",
            "preferred orientation is tested one sample phase at a time with an evidence-bound March-Dollase axis and is never auto-promoted solely by lower GOF",
            "microabsorption sensitivity uses externally justified true-mass multiplier intervals; GSAS-II does not refine those factors",
            "amorphous content is calculated only for exactly one internal standard with a known, evidence-bound added mass fraction",
            "trace-phase sigma classes are not a validated instrumental LOD/LOQ and require spike-in or profile-likelihood validation for formal claims",
            "dopant content or site occupancy is not freely refined; compare only frozen evidence-bound CIF variants in a constrained model grid",
            "no figure generated",
        ],
    }
    write_json_atomic(output_dir / "qpa_summary.json", summary)
    prediction = {
        "schema_version": 4,
        "sample_id": args.sample_id,
        "held_out_id": args.held_out_id,
        "prediction_archived_at": datetime.now(timezone.utc).isoformat(),
        "answer_status": args.answer_status,
        "selected_candidate": summary["selected_candidate"],
        "status": status,
        "mass_fractions": (
            selected.get("sample_normalized_mass_fractions") if selected else None
        ),
        "all_modeled_crystalline_scale_fractions": (
            selected.get("refined_mass_fractions") if selected else None
        ),
        "phase_roles": phase_roles,
        "internal_standard_present": any(
            role == "internal_standard" for role in phase_roles.values()
        ),
        "metrics": selected.get("metrics") if selected else None,
        "reported_uncertainties": reported_uncertainties,
        "cell_model_fraction_spread": cell_model_spread,
        "broadening_model_fraction_spread": broadening_model_spread,
        "preferred_orientation_model_fraction_spread": preferred_orientation_model_spread,
        "microabsorption_model_fraction_spread": microabsorption_model_spread,
        "amorphous_content": amorphous_content,
        "trace_phase_assessment": trace_phase_assessment,
        "repeatability": repeatability,
        "phase_model_audit_sha256": sha256_file(output_dir / "phase_model_audit.json"),
        "protocol_manifest_sha256": sha256_file(output_dir / "protocol_manifest.json"),
        "qpa_summary_sha256_before_prediction_archive": sha256_file(output_dir / "qpa_summary.json"),
    }
    write_json_atomic(output_dir / "prediction_archive.json", prediction)
    print(json.dumps(json_clean({"status": status, "selected": summary["selected_candidate"], "summary": str(output_dir / "qpa_summary.json"), "prediction_archive": str(output_dir / "prediction_archive.json")}), indent=2))
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
