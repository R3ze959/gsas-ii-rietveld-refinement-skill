#!/usr/bin/env python3
"""Pure helpers shared by multiphase QPA drivers and tests."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def phase_b_scale_for_mass_fraction(
    phase_b_fraction: float,
    phase_a_mass: float,
    phase_b_mass: float,
    phase_a_scale: float = 1.0,
) -> float:
    if not 0.0 < phase_b_fraction < 1.0:
        raise ValueError("phase_b_fraction must be strictly between 0 and 1")
    if min(phase_a_mass, phase_b_mass, phase_a_scale) <= 0.0:
        raise ValueError("phase masses and reference scale must be positive")
    phase_a_fraction = 1.0 - phase_b_fraction
    return (
        phase_b_fraction
        * phase_a_mass
        * phase_a_scale
        / (phase_b_mass * phase_a_fraction)
    )


def mass_normalized_scales(
    phase_b_fraction: float,
    phase_a_mass: float,
    phase_b_mass: float,
    total_weighted_scale: float,
) -> tuple[float, float]:
    """Return HAP scales with a constant mass-weighted total.

    GSAS-II phase mass fractions are proportional to ``mass * HAP scale``.
    Keeping their sum constant prevents a change in phase composition from also
    changing the simulated specimen amount.
    """
    if not 0.0 < phase_b_fraction < 1.0:
        raise ValueError("phase_b_fraction must be strictly between 0 and 1")
    if min(phase_a_mass, phase_b_mass, total_weighted_scale) <= 0.0:
        raise ValueError("phase masses and total_weighted_scale must be positive")
    phase_a_fraction = 1.0 - phase_b_fraction
    return (
        phase_a_fraction * total_weighted_scale / phase_a_mass,
        phase_b_fraction * total_weighted_scale / phase_b_mass,
    )


def mass_fractions_from_scales(
    phase_a_scale: float,
    phase_b_scale: float,
    phase_a_mass: float,
    phase_b_mass: float,
) -> tuple[float, float]:
    weighted_a = phase_a_scale * phase_a_mass
    weighted_b = phase_b_scale * phase_b_mass
    total = weighted_a + weighted_b
    if total <= 0.0:
        raise ValueError("weighted scale sum must be positive")
    return weighted_a / total, weighted_b / total


def scales_for_mass_fractions(
    fractions: dict[str, float],
    masses: dict[str, float],
    anchor: str,
    *,
    anchor_scale: float = 1.0,
) -> dict[str, float]:
    """Convert an N-phase mass-fraction start into identifiable HAP scales."""
    if set(fractions) != set(masses):
        raise ValueError("fractions and masses must contain the same phase names")
    if anchor not in fractions:
        raise ValueError(f"anchor phase is missing: {anchor}")
    if anchor_scale <= 0.0 or any(value <= 0.0 for value in masses.values()):
        raise ValueError("anchor scale and phase masses must be positive")
    if any(not math.isfinite(value) or value <= 0.0 for value in fractions.values()):
        raise ValueError("starting fractions must be finite and strictly positive")
    total = sum(fractions.values())
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-8):
        raise ValueError(f"starting fractions must sum to 1; received {total}")
    anchor_fraction = fractions[anchor]
    anchor_mass = masses[anchor]
    return {
        name: (
            anchor_scale
            if name == anchor
            else fraction * anchor_mass * anchor_scale
            / (anchor_fraction * masses[name])
        )
        for name, fraction in fractions.items()
    }


def maximum_correlation(
    vary_list: list[str], covariance: Any
) -> dict[str, Any]:
    import numpy as np

    matrix = np.asarray(covariance, dtype=float)
    if matrix.shape != (len(vary_list), len(vary_list)):
        return {"absolute": None, "signed": None, "parameters": []}
    best_absolute = -1.0
    best_signed = None
    best_pair: list[str] = []
    for row in range(len(vary_list)):
        for column in range(row + 1, len(vary_list)):
            denominator = math.sqrt(matrix[row, row] * matrix[column, column])
            if denominator <= 0.0:
                continue
            signed = float(matrix[row, column] / denominator)
            if abs(signed) > best_absolute:
                best_absolute = abs(signed)
                best_signed = signed
                best_pair = [vary_list[row], vary_list[column]]
    return {
        "absolute": None if best_signed is None else best_absolute,
        "signed": best_signed,
        "parameters": best_pair,
    }


def element_symbol(value: str) -> str:
    """Return a neutral element symbol from a CIF/GSAS-II atom type."""
    match = re.match(r"\s*([A-Z][a-z]?)", str(value))
    if not match:
        raise ValueError(f"cannot determine element symbol from {value!r}")
    return match.group(1)


def compare_phase_compositions(
    source: dict[str, float],
    imported: dict[str, float],
    *,
    absolute_tolerance: float = 0.05,
    relative_tolerance: float = 0.005,
) -> dict[str, Any]:
    """Compare independent CIF and GSAS-II unit-cell atom counts."""
    if absolute_tolerance < 0.0 or relative_tolerance < 0.0:
        raise ValueError("composition tolerances must be nonnegative")
    elements = sorted(set(source) | set(imported))
    rows = []
    for symbol in elements:
        source_value = float(source.get(symbol, 0.0))
        imported_value = float(imported.get(symbol, 0.0))
        difference = imported_value - source_value
        tolerance = max(absolute_tolerance, relative_tolerance * abs(source_value))
        rows.append(
            {
                "element": symbol,
                "source_cell_count": source_value,
                "imported_cell_count": imported_value,
                "difference": difference,
                "tolerance": tolerance,
                "within_tolerance": abs(difference) <= tolerance,
            }
        )
    return {
        "status": "pass" if rows and all(row["within_tolerance"] for row in rows) else "fail",
        "elements": rows,
    }


def assess_phase_model_import(
    *,
    source_composition: dict[str, float],
    imported_composition: dict[str, float],
    expected_cell_mass: float,
    imported_cell_mass: float,
    import_log: str = "",
    occupancy_issues: list[str] | None = None,
    mass_relative_tolerance: float = 0.005,
) -> dict[str, Any]:
    """Gate a GSAS-II CIF import against independently parsed CIF content."""
    if expected_cell_mass <= 0.0 or imported_cell_mass <= 0.0:
        raise ValueError("phase masses must be positive")
    if mass_relative_tolerance < 0.0:
        raise ValueError("mass_relative_tolerance must be nonnegative")
    composition = compare_phase_compositions(source_composition, imported_composition)
    mass_relative_difference = abs(imported_cell_mass - expected_cell_mass) / expected_cell_mass
    log_lower = import_log.lower()
    incompatible_setting = (
        any(
            phrase in log_lower
            for phrase in (
                "not compatible with gsas-ii",
                "not matched in gsas-ii setting",
                "space group setting not compatible",
            )
        )
        or (
            "not compatible" in log_lower
            and any(word in log_lower for word in ("space group", "setting", "gsas"))
        )
        or (
            "not matched" in log_lower
            and "symmetr" in log_lower
            and "gsas" in log_lower
        )
    )
    hard_failures: list[str] = []
    review_flags: list[str] = []
    if composition["status"] != "pass":
        hard_failures.append("imported_unit_cell_composition_mismatch")
    if mass_relative_difference > mass_relative_tolerance:
        hard_failures.append("imported_unit_cell_mass_mismatch")
    if incompatible_setting:
        hard_failures.append("gsasii_incompatible_space_group_setting")
    if occupancy_issues:
        hard_failures.extend(f"invalid_source_occupancy:{item}" for item in occupancy_issues)
    if import_log.strip() and not incompatible_setting:
        review_flags.append("gsasii_import_emitted_messages")
    return {
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "hard_failures": list(dict.fromkeys(hard_failures)),
        "review_flags": list(dict.fromkeys(review_flags)),
        "composition_comparison": composition,
        "expected_cell_mass": expected_cell_mass,
        "imported_cell_mass": imported_cell_mass,
        "mass_relative_difference": mass_relative_difference,
        "mass_relative_tolerance": mass_relative_tolerance,
        "incompatible_setting_message_detected": incompatible_setting,
    }


def phase_set_completeness_gate(
    phase_set_status: str,
    *,
    evidence: str | None,
    held_out: bool,
) -> dict[str, Any]:
    """Make the declared-phase boundary explicit before quantitative fitting."""
    if phase_set_status not in {"verified", "provisional", "unknown"}:
        raise ValueError(f"unsupported phase-set status: {phase_set_status}")
    hard_failures: list[str] = []
    review_flags: list[str] = []
    if phase_set_status == "unknown":
        hard_failures.append("phase_set_not_established")
    elif phase_set_status == "provisional":
        review_flags.append("phase_set_provisional")
        if held_out:
            hard_failures.append("held_out_qpa_requires_verified_phase_set")
    elif not evidence or not evidence.strip():
        hard_failures.append("verified_phase_set_requires_evidence")
    return {
        "phase_set_status": phase_set_status,
        "evidence": evidence,
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "hard_failures": hard_failures,
        "review_flags": review_flags,
    }


def residual_peak_audit(histogram: Any, *, count: int = 12) -> dict[str, Any]:
    """Summarize positive local residual maxima without creating a figure."""
    import numpy as np

    arrays = histogram.data["data"][1]
    x = np.asarray(arrays[0], dtype=float)
    observed = np.asarray(arrays[1], dtype=float)
    calculated = np.asarray(arrays[3], dtype=float)
    background = np.asarray(arrays[4], dtype=float)
    residual = np.asarray(arrays[5], dtype=float)
    finite = (
        np.isfinite(x)
        & np.isfinite(observed)
        & np.isfinite(calculated)
        & np.isfinite(background)
        & np.isfinite(residual)
    )
    x, observed, calculated, background, residual = (
        array[finite] for array in (x, observed, calculated, background, residual)
    )
    if residual.size < 3:
        return {
            "positive_local_maxima": [],
            "robust_residual_sigma": None,
            "durbin_watson_unweighted": None,
            "pattern_maximum_observed": None,
        }
    modeled_profile = np.maximum(calculated - background, 0.0)
    median = float(np.median(residual))
    sigma = float(1.4826 * np.median(np.abs(residual - median)))
    pattern_maximum = float(np.max(np.abs(observed)))
    local = np.where(
        (residual[1:-1] >= residual[:-2])
        & (residual[1:-1] >= residual[2:])
        & (residual[1:-1] > 0.0)
    )[0] + 1
    ordered = local[np.argsort(residual[local])[::-1]]
    selected: list[int] = []
    for index in ordered:
        if all(abs(x[index] - x[kept]) >= 0.08 for kept in selected):
            selected.append(int(index))
        if len(selected) >= count:
            break
    denominator = float(np.sum(residual**2))
    peaks = []
    for index in selected:
        peaks.append(
            {
                "two_theta": float(x[index]),
                "obs_minus_calc": float(residual[index]),
                "residual_percent_of_pattern_maximum": (
                    100.0 * float(residual[index]) / pattern_maximum
                    if pattern_maximum > 0.0
                    else None
                ),
                "modeled_profile_percent_of_pattern_maximum": (
                    100.0 * float(modeled_profile[index]) / pattern_maximum
                    if pattern_maximum > 0.0
                    else None
                ),
                "robust_sigma_above_residual": (
                    (float(residual[index]) - median) / sigma if sigma > 0.0 else None
                ),
            }
        )
    return {
        "positive_local_maxima": peaks,
        "robust_residual_sigma": sigma,
        "durbin_watson_unweighted": (
            float(np.sum(np.diff(residual) ** 2) / denominator)
            if denominator > 0.0
            else None
        ),
        "pattern_maximum_observed": pattern_maximum,
    }


def assess_real_qpa_candidate(
    candidate: dict[str, Any],
    *,
    correlation_limit: float = 0.95,
    shift_limit: float = 0.01,
    residual_minimum_sigma: float = 6.0,
    residual_minimum_pattern_percent: float = 2.0,
    residual_hard_pattern_percent: float = 10.0,
    residual_modeled_peak_minimum_percent: float = 1.0,
) -> dict[str, Any]:
    """Apply answer-independent scientific gates to one real-pattern fit."""
    metrics = candidate.get("metrics", {})
    hard_failures: list[str] = []
    review_flags: list[str] = []
    if not metrics.get("converged", False):
        hard_failures.append("nonconverged")
    if int(metrics.get("svd_count", 0) or 0):
        hard_failures.append(f"svd_count={metrics.get('svd_count')}")
    fractions = candidate.get("refined_mass_fractions", {})
    values = [item.get("value") for item in fractions.values()]
    if (
        not values
        or any(value is None or not math.isfinite(float(value)) or float(value) < 0.0 for value in values)
        or not math.isclose(sum(float(value) for value in values), 1.0, abs_tol=1e-5)
    ):
        hard_failures.append("invalid_mass_fractions")
    for name, item in fractions.items():
        esd = item.get("esd")
        if esd is None or not math.isfinite(float(esd)) or float(esd) <= 0.0:
            review_flags.append(f"missing_or_nonpositive_esd:{name}")
    correlation = metrics.get("maximum_correlation", {}).get("absolute")
    if correlation is None:
        review_flags.append("correlation_unavailable")
    elif float(correlation) >= correlation_limit:
        review_flags.append("high_correlation")
    shift = metrics.get("max_shift_over_esd")
    if shift is None:
        review_flags.append("shift_over_esd_unavailable")
    elif abs(float(shift)) > shift_limit:
        review_flags.append("shift_over_esd_exceeds_limit")
    for peak in candidate.get("residual_audit", {}).get("positive_local_maxima", []):
        sigma = peak.get("robust_sigma_above_residual")
        percent = peak.get("residual_percent_of_pattern_maximum")
        modeled = peak.get("modeled_profile_percent_of_pattern_maximum")
        if sigma is None or percent is None:
            continue
        if float(sigma) < residual_minimum_sigma or float(percent) < residual_minimum_pattern_percent:
            continue
        label = f"{float(peak['two_theta']):.4f}deg"
        if float(percent) >= residual_hard_pattern_percent:
            hard_failures.append(f"major_profile_or_phase_model_residual:{label}")
        elif modeled is not None and float(modeled) >= residual_modeled_peak_minimum_percent:
            review_flags.append(f"modeled_peak_residual:{label}")
        else:
            review_flags.append(f"unexplained_residual:{label}")
    status = "fail" if hard_failures else "review" if review_flags else "pass"
    return {
        "status": status,
        "hard_failures": list(dict.fromkeys(hard_failures)),
        "review_flags": list(dict.fromkeys(review_flags)),
    }


def evaluate_training_case(
    *,
    target_fraction: float,
    refined_fraction: float | None,
    refined_esd: float | None,
    converged: bool,
    svd_count: int,
    max_shift_over_esd: float | None,
    max_correlation: float | None,
    absolute_error_limit: float,
    correlation_limit: float,
    shift_limit: float,
) -> dict[str, Any]:
    hard_failures: list[str] = []
    review_flags: list[str] = []
    if not converged:
        hard_failures.append("nonconverged")
    if svd_count != 0:
        hard_failures.append(f"svd_count={svd_count}")
    if refined_fraction is None or not 0.0 <= refined_fraction <= 1.0:
        hard_failures.append("invalid_mass_fraction")
    if refined_esd is None or not math.isfinite(refined_esd) or refined_esd <= 0.0:
        review_flags.append("missing_or_nonpositive_esd")
    absolute_error = (
        None if refined_fraction is None else abs(refined_fraction - target_fraction)
    )
    if absolute_error is not None and absolute_error > absolute_error_limit:
        review_flags.append("mass_fraction_error_exceeds_limit")
    if max_correlation is None:
        review_flags.append("correlation_unavailable")
    elif max_correlation >= correlation_limit:
        review_flags.append("high_correlation")
    if max_shift_over_esd is None:
        review_flags.append("shift_over_esd_unavailable")
    elif abs(max_shift_over_esd) > shift_limit:
        review_flags.append("shift_over_esd_exceeds_limit")
    status = "fail" if hard_failures else "review" if review_flags else "pass"
    return {
        "status": status,
        "absolute_error": absolute_error,
        "hard_failures": hard_failures,
        "review_flags": review_flags,
    }


def candidate_selection_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """Rank a refinement path without consulting the simulated ground truth."""
    metrics = candidate.get("metrics", {})
    converged = bool(metrics.get("converged", False))
    svd_count = int(metrics.get("svd_count", 0) or 0)
    fractions = candidate.get("refined_mass_fractions", {})
    values = [entry.get("value") for entry in fractions.values()]
    valid_fractions = (
        bool(values)
        and all(value is not None and math.isfinite(value) and 0.0 <= value <= 1.0 for value in values)
        and abs(sum(values) - 1.0) <= 1e-5
    )
    fallback_failures = int(not converged) + int(svd_count != 0) + int(not valid_fractions)
    assessment = candidate.get("assessment")
    hard_failure_count = (
        len(set(assessment.get("hard_failures", [])))
        if isinstance(assessment, dict)
        else fallback_failures
    )
    gof = metrics.get("GOF")
    gof_rank = float(gof) if gof is not None and math.isfinite(float(gof)) else math.inf
    correlation = metrics.get("maximum_correlation", {}).get("absolute")
    correlation_rank = (
        float(correlation)
        if correlation is not None and math.isfinite(float(correlation))
        else math.inf
    )
    shift = metrics.get("max_shift_over_esd")
    shift_rank = (
        abs(float(shift))
        if shift is not None and math.isfinite(float(shift))
        else math.inf
    )
    return (
        hard_failure_count,
        gof_rank,
        correlation_rank,
        shift_rank,
        str(candidate.get("candidate", "")),
    )


def select_competitive_candidate(
    candidates: list[dict[str, Any]], *, relative_gof_tolerance: float = 1e-8
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Select among statistically indistinguishable paths without using truth.

    Candidates with the minimum hard-failure count are retained. Among those,
    paths whose GOF is within a small relative tolerance of the best are treated
    as equivalent fits and ranked by conditioning, trajectory shift and name.
    """
    if not candidates:
        raise ValueError("at least one candidate is required")
    if relative_gof_tolerance < 0.0:
        raise ValueError("relative_gof_tolerance must be nonnegative")
    keyed = [(candidate_selection_key(candidate), candidate) for candidate in candidates]
    minimum_failures = min(key[0] for key, _ in keyed)
    viable = [(key, candidate) for key, candidate in keyed if key[0] == minimum_failures]
    best_gof = min(key[1] for key, _ in viable)
    threshold = best_gof * (1.0 + relative_gof_tolerance)
    competitive = [candidate for key, candidate in viable if key[1] <= threshold]

    def conditioning_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
        key = candidate_selection_key(candidate)
        return key[2], key[3], key[4]

    return min(competitive, key=conditioning_key), competitive


def summarize_qpa_repeatability(
    records: list[dict[str, Any]],
    phase_names: list[str],
    *,
    fraction_range_limit: float,
    fraction_key: str = "refined_mass_fractions",
) -> dict[str, Any]:
    """Audit exact final-model reruns without consulting a reference answer."""
    if len(records) < 2:
        raise ValueError("repeatability audit requires at least two records")
    if fraction_range_limit <= 0.0:
        raise ValueError("fraction_range_limit must be positive")
    hard_failures: list[str] = []
    rows: dict[str, Any] = {}
    usable_records = []
    for record in records:
        if not record.get(fraction_key):
            continue
        metrics = record.get("metrics")
        if metrics is not None and (
            not metrics.get("converged", False)
            or int(metrics.get("svd_count", 0) or 0) != 0
        ):
            continue
        usable_records.append(record)
    if len(usable_records) != len(records):
        hard_failures.append("repeatability_run_failed")
    for name in phase_names:
        values = []
        for record in usable_records:
            item = record.get(fraction_key, {}).get(name, {})
            value = item.get("value")
            if value is not None and math.isfinite(float(value)):
                values.append(float(value))
        value_range = max(values) - min(values) if len(values) == len(records) else None
        within = value_range is not None and value_range <= fraction_range_limit
        rows[name] = {
            "values": values,
            "range": value_range,
            "range_limit": fraction_range_limit,
            "within_limit": within,
        }
        if value_range is None:
            hard_failures.append(f"repeatability_insufficient_usable_values:{name}")
        elif not within:
            hard_failures.append(f"repeatability_fraction_range_exceeds_limit:{name}")
    return {
        "status": "fail" if hard_failures else "pass",
        "run_count": len(records),
        "usable_run_count": len(usable_records),
        "phases": rows,
        "hard_failures": list(dict.fromkeys(hard_failures)),
    }


def normalized_mass_fractions_with_covariance(
    *,
    scales: dict[str, float],
    masses: dict[str, float],
    varying_scale_indices: dict[str, int],
    covariance: Any,
) -> dict[str, dict[str, float]]:
    """Normalize a declared phase subset and propagate HAP-scale covariance."""
    import numpy as np

    names = list(scales)
    if not names or set(names) != set(masses):
        raise ValueError("scale and mass phase sets must be equal and nonempty")
    weighted_sum = sum(float(scales[name]) * float(masses[name]) for name in names)
    if not math.isfinite(weighted_sum) or weighted_sum <= 0.0:
        raise ValueError("normalized phase subset has nonpositive total mass scale")
    matrix = np.asarray(covariance, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("covariance matrix must be square")
    for name, index in varying_scale_indices.items():
        if name not in scales or not 0 <= int(index) < matrix.shape[0]:
            raise ValueError("varying scale index does not match the phase subset")
    result: dict[str, dict[str, float]] = {}
    for target in names:
        target_mass = float(masses[target])
        target_scale = float(scales[target])
        value = target_mass * target_scale / weighted_sum
        derivative = np.zeros(matrix.shape[0], dtype=float)
        for varied, index in varying_scale_indices.items():
            varied_mass = float(masses[varied])
            if varied == target:
                derivative[int(index)] = (
                    target_mass / weighted_sum
                    - target_mass * target_scale * varied_mass / weighted_sum**2
                )
            else:
                derivative[int(index)] = (
                    -target_mass * target_scale * varied_mass / weighted_sum**2
                )
        variance = float(derivative @ matrix @ derivative)
        if variance < 0.0 and abs(variance) < 1e-18:
            variance = 0.0
        result[target] = {
            "value": value,
            "esd": math.sqrt(variance) if variance >= 0.0 else math.nan,
        }
    return result


def summarize_replicates(
    cases: list[dict[str, Any]], *, nominal_fraction: float
) -> dict[str, Any]:
    """Summarize bias, RMSE and uncertainty coverage for one nominal mixture."""
    usable = [
        case
        for case in cases
        if case.get("selected_fraction") is not None
        and math.isfinite(float(case["selected_fraction"]))
    ]
    errors = [float(case["selected_fraction"]) - nominal_fraction for case in usable]
    esds = [case.get("selected_esd") for case in usable]
    bias = sum(errors) / len(errors) if errors else None
    rmse = math.sqrt(sum(error * error for error in errors) / len(errors)) if errors else None

    def coverage(multiplier: float) -> float | None:
        scored = [
            (error, float(esd))
            for error, esd in zip(errors, esds)
            if esd is not None and math.isfinite(float(esd)) and float(esd) > 0.0
        ]
        if not scored:
            return None
        return sum(abs(error) <= multiplier * esd for error, esd in scored) / len(scored)

    statuses = [str(case.get("status", "fail")) for case in cases]
    anchors: dict[str, int] = {}
    for case in cases:
        anchor = str(case.get("selected_anchor", "unknown"))
        anchors[anchor] = anchors.get(anchor, 0) + 1
    return {
        "replicate_count": len(cases),
        "usable_count": len(usable),
        "bias": bias,
        "rmse": rmse,
        "maximum_absolute_error": max((abs(error) for error in errors), default=None),
        "coverage_1sigma": coverage(1.0),
        "coverage_2sigma": coverage(2.0),
        "status_counts": {
            "pass": statuses.count("pass"),
            "review": statuses.count("review"),
            "fail": statuses.count("fail"),
        },
        "selected_anchor_counts": anchors,
    }


def json_clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_clean(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, Path):
        return str(value)
    return value


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(json_clean(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
