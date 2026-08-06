#!/usr/bin/env python3
"""Deterministic gates for common multiphase-QPA failure modes.

The helpers in this module do not call GSAS-II. They convert explicitly
declared sensitivity results into auditable scientific decisions and keep the
meaning of every correction separate from the profile refinement itself.
"""

from __future__ import annotations

import math
from typing import Any


EVIDENCE_SCHEMA_VERSION = 1
MICROABSORPTION_MULTIPLIER_DEFINITION = (
    "true crystalline mass contribution = Rietveld contribution * multiplier"
)


def _validate_evidence_envelope(
    data: dict[str, Any], *, kind: str
) -> dict[str, Any]:
    """Validate the non-negotiable fields shared by scientific evidence files."""
    if not isinstance(data, dict):
        raise ValueError("evidence must contain a JSON object")
    version = data.get("schema_version")
    if isinstance(version, bool) or version != EVIDENCE_SCHEMA_VERSION:
        raise ValueError(
            f"evidence schema_version must be {EVIDENCE_SCHEMA_VERSION}"
        )
    if data.get("kind") != kind:
        raise ValueError(f"evidence kind must be {kind!r}")
    if data.get("status") != "pass":
        raise ValueError("evidence status must be 'pass'")
    if data.get("scientific_claim") is False:
        raise ValueError("evidence explicitly disables scientific use")
    if not data.get("basis"):
        raise ValueError("evidence must provide a nonempty basis")
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "kind": kind,
        "status": "pass",
        "basis_present": True,
    }


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        left_value = float(left)
        right_value = float(right)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(left_value)
        and math.isfinite(right_value)
        and math.isclose(left_value, right_value, rel_tol=1e-12, abs_tol=1e-12)
    )


def validate_common_problem_evidence(
    data: dict[str, Any],
    *,
    kind: str,
    policy: str | None = None,
    expected_phase_axes: dict[str, tuple[int, int, int]] | None = None,
    expected_phase_intervals: dict[str, tuple[float, float]] | None = None,
    expected_standard_phase: str | None = None,
    expected_added_fraction: float | None = None,
    expected_added_fraction_esd: float | None = None,
) -> dict[str, Any]:
    """Validate that a hash-bound file supports the exact declared branch.

    Hashing proves file identity, not scientific meaning. This validator makes
    the evidence kind, policy and numerical declarations part of the frozen
    protocol and rejects generic or contradictory JSON files.
    """
    record = _validate_evidence_envelope(data, kind=kind)
    if kind in {
        "preferred_orientation_assessment",
        "microabsorption_assessment",
    }:
        if policy not in {"assessed_negligible", "sensitivity"}:
            raise ValueError(f"unsupported evidence policy for {kind}: {policy!r}")
        if data.get("policy") != policy:
            raise ValueError(f"evidence policy must be {policy!r}")
        record["policy"] = policy
        expected_conclusion = (
            "negligible" if policy == "assessed_negligible" else "sensitivity_defined"
        )
        if data.get("conclusion") != expected_conclusion:
            raise ValueError(
                f"evidence conclusion must be {expected_conclusion!r} for policy {policy!r}"
            )
        record["conclusion"] = expected_conclusion

    if kind == "preferred_orientation_assessment" and policy == "sensitivity":
        expected = {
            name: list(axis) for name, axis in sorted((expected_phase_axes or {}).items())
        }
        supplied = data.get("phase_axes")
        if not isinstance(supplied, dict) or supplied != expected:
            raise ValueError(
                "preferred-orientation evidence phase_axes do not match the CLI declaration"
            )
        record["phase_axes"] = expected
    elif kind == "microabsorption_assessment" and policy == "sensitivity":
        expected = {
            name: [float(bounds[0]), float(bounds[1])]
            for name, bounds in sorted((expected_phase_intervals or {}).items())
        }
        supplied = data.get("phase_intervals")
        if not isinstance(supplied, dict) or set(supplied) != set(expected):
            raise ValueError(
                "microabsorption evidence phase_intervals do not match the CLI declaration"
            )
        for name, expected_bounds in expected.items():
            supplied_bounds = supplied.get(name)
            if (
                not isinstance(supplied_bounds, list)
                or len(supplied_bounds) != 2
                or not all(
                    _same_number(actual, target)
                    for actual, target in zip(supplied_bounds, expected_bounds)
                )
            ):
                raise ValueError(
                    "microabsorption evidence phase_intervals do not match the CLI declaration"
                )
        if data.get("multiplier_definition") != MICROABSORPTION_MULTIPLIER_DEFINITION:
            raise ValueError("microabsorption evidence uses the wrong multiplier definition")
        record["phase_intervals"] = expected
        record["multiplier_definition"] = MICROABSORPTION_MULTIPLIER_DEFINITION
    elif kind == "internal_standard_addition":
        if not expected_standard_phase:
            raise ValueError("expected internal-standard phase is required")
        if data.get("standard_phase") != expected_standard_phase:
            raise ValueError("internal-standard evidence names a different standard phase")
        if data.get("basis") != "post_addition_total_mass":
            raise ValueError(
                "internal-standard evidence basis must be 'post_addition_total_mass'"
            )
        if data.get("conclusion") != "addition_record_verified":
            raise ValueError(
                "internal-standard evidence conclusion must be 'addition_record_verified'"
            )
        if not _same_number(
            data.get("added_fraction_after_mixing"), expected_added_fraction
        ):
            raise ValueError(
                "internal-standard evidence added fraction does not match the CLI declaration"
            )
        if not _same_number(
            data.get("added_fraction_esd"), expected_added_fraction_esd
        ):
            raise ValueError(
                "internal-standard evidence fraction ESD does not match the CLI declaration"
            )
        record.update(
            {
                "standard_phase": expected_standard_phase,
                "basis": "post_addition_total_mass",
                "conclusion": "addition_record_verified",
                "added_fraction_after_mixing": float(expected_added_fraction),
                "added_fraction_esd": (
                    None
                    if expected_added_fraction_esd is None
                    else float(expected_added_fraction_esd)
                ),
            }
        )
    elif kind not in {
        "preferred_orientation_assessment",
        "microabsorption_assessment",
    }:
        raise ValueError(f"unsupported common-problem evidence kind: {kind!r}")
    return record


def validate_model_grid_evidence(
    data: dict[str, Any], *, target_phase: str, variant_labels: list[str]
) -> dict[str, Any]:
    """Validate a predeclared constrained-CIF model-grid contract."""
    record = _validate_evidence_envelope(data, kind="constrained_model_grid")
    expected_labels = sorted(str(item) for item in variant_labels)
    if data.get("target_phase") != target_phase:
        raise ValueError("model-grid evidence target_phase does not match the CLI")
    supplied_labels = data.get("variant_labels")
    if (
        not isinstance(supplied_labels, list)
        or not all(isinstance(item, str) for item in supplied_labels)
        or len(set(supplied_labels)) != len(supplied_labels)
        or sorted(supplied_labels) != expected_labels
    ):
        raise ValueError("model-grid evidence variant_labels do not match the CLI")
    if data.get("claim_scope") != "model_comparison_only":
        raise ValueError("model-grid evidence claim_scope must be 'model_comparison_only'")
    record.update(
        {
            "target_phase": target_phase,
            "variant_labels": expected_labels,
            "claim_scope": "model_comparison_only",
        }
    )
    return record


def parse_hkl(value: str) -> tuple[int, int, int]:
    """Parse one nonzero March-Dollase axis from ``h,k,l`` text."""
    parts = [item.strip() for item in str(value).split(",")]
    if len(parts) != 3:
        raise ValueError(f"preferred-orientation axis must be h,k,l: {value!r}")
    try:
        axis = tuple(int(item) for item in parts)
    except ValueError as exc:
        raise ValueError(
            f"preferred-orientation axis must contain integers: {value!r}"
        ) from exc
    if axis == (0, 0, 0):
        raise ValueError("preferred-orientation axis cannot be 0,0,0")
    return axis


def parse_positive_interval(value: str) -> tuple[float, float]:
    """Parse an inclusive positive interval from ``LOW,HIGH`` text."""
    parts = [item.strip() for item in str(value).split(",")]
    if len(parts) != 2:
        raise ValueError(f"interval must be LOW,HIGH: {value!r}")
    try:
        low, high = (float(item) for item in parts)
    except ValueError as exc:
        raise ValueError(f"interval must contain numbers: {value!r}") from exc
    if not all(math.isfinite(item) and item > 0.0 for item in (low, high)):
        raise ValueError("interval bounds must be finite and positive")
    if low > high:
        raise ValueError("interval lower bound cannot exceed upper bound")
    return low, high


def merge_assessments(*assessments: dict[str, Any] | None) -> dict[str, Any]:
    """Merge hard failures and review flags with fail > review > pass."""
    hard_failures: list[str] = []
    review_flags: list[str] = []
    for assessment in assessments:
        if not assessment:
            continue
        hard_failures.extend(str(item) for item in assessment.get("hard_failures", []))
        review_flags.extend(str(item) for item in assessment.get("review_flags", []))
    hard_failures = list(dict.fromkeys(hard_failures))
    review_flags = list(dict.fromkeys(review_flags))
    return {
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "hard_failures": hard_failures,
        "review_flags": review_flags,
    }


def assess_preferred_orientation_sensitivity(
    baseline: dict[str, Any],
    trials: list[dict[str, Any]],
    *,
    correlation_limit: float = 0.95,
    shift_limit: float = 0.01,
    minimum_relative_gof_improvement: float = 0.005,
    fraction_spread_review_limit: float = 0.01,
    ratio_bounds: tuple[float, float] = (0.2, 5.0),
) -> dict[str, Any]:
    """Assess one-phase-at-a-time March-Dollase sensitivity trials.

    A materially better, physically safe preferred-orientation trial is kept
    as ``review`` rather than promoted automatically. The model requires
    specimen/morphology evidence and should be rerun as a separately declared
    primary model if the user elects to use it.
    """
    if not trials:
        return {
            "status": "review",
            "hard_failures": [],
            "review_flags": ["preferred_orientation_sensitivity_missing"],
            "trials": [],
            "phase_fraction_spread": {},
        }
    baseline_gof = baseline.get("metrics", {}).get("GOF")
    baseline_fractions = baseline.get("sample_normalized_mass_fractions", {})
    if baseline_gof is None or not math.isfinite(float(baseline_gof)) or float(baseline_gof) <= 0.0:
        raise ValueError("baseline GOF must be finite and positive")
    if not baseline_fractions:
        raise ValueError("baseline sample-normalized fractions are required")
    low_ratio, high_ratio = ratio_bounds
    if not 0.0 < low_ratio < high_ratio:
        raise ValueError("preferred-orientation ratio bounds are invalid")
    review_flags: list[str] = []
    rows = []
    phase_spread = {name: 0.0 for name in baseline_fractions}
    for trial in trials:
        phase = str(trial.get("preferred_orientation", {}).get("phase", "unknown"))
        metrics = trial.get("metrics", {})
        ratio = trial.get("preferred_orientation", {}).get("ratio")
        ratio_esd = trial.get("preferred_orientation", {}).get("esd")
        trial_gof = metrics.get("GOF")
        fractions = trial.get("sample_normalized_mass_fractions", {})
        correlation = metrics.get("maximum_correlation", {}).get("absolute")
        shift = metrics.get("max_shift_over_esd")
        valid_gof = (
            trial_gof is not None
            and math.isfinite(float(trial_gof))
            and float(trial_gof) > 0.0
        )
        gof_improvement = (
            (float(baseline_gof) - float(trial_gof)) / float(baseline_gof)
            if valid_gof
            else None
        )
        physical_ratio = (
            ratio is not None
            and math.isfinite(float(ratio))
            and low_ratio <= float(ratio) <= high_ratio
        )
        numerical_safe = (
            bool(metrics.get("converged", False))
            and int(metrics.get("svd_count", 0) or 0) == 0
            and correlation is not None
            and math.isfinite(float(correlation))
            and float(correlation) < correlation_limit
            and shift is not None
            and math.isfinite(float(shift))
            and abs(float(shift)) <= shift_limit
            and valid_gof
            and physical_ratio
            and ratio_esd is not None
            and math.isfinite(float(ratio_esd))
            and float(ratio_esd) > 0.0
        )
        maximum_fraction_change = None
        if set(fractions) == set(baseline_fractions):
            changes = {}
            for name in baseline_fractions:
                change = abs(
                    float(fractions[name]["value"])
                    - float(baseline_fractions[name]["value"])
                )
                changes[name] = change
                phase_spread[name] = max(phase_spread[name], change)
            maximum_fraction_change = max(changes.values(), default=0.0)
        else:
            numerical_safe = False
            review_flags.append(f"preferred_orientation_fraction_set_mismatch:{phase}")
        reasons = []
        if not bool(metrics.get("converged", False)):
            reasons.append("nonconverged")
        if int(metrics.get("svd_count", 0) or 0):
            reasons.append("svd_failure")
        if correlation is None or not math.isfinite(float(correlation)):
            reasons.append("correlation_unavailable")
        elif float(correlation) >= correlation_limit:
            reasons.append("high_correlation")
        if shift is None or not math.isfinite(float(shift)):
            reasons.append("shift_over_esd_unavailable")
        elif abs(float(shift)) > shift_limit:
            reasons.append("shift_over_esd_exceeds_limit")
        if not physical_ratio:
            reasons.append("nonphysical_march_dollase_ratio")
        if (
            ratio_esd is None
            or not math.isfinite(float(ratio_esd))
            or float(ratio_esd) <= 0.0
        ):
            reasons.append("march_dollase_uncertainty_missing")
        if not valid_gof:
            reasons.append("invalid_gof")
        material = (
            numerical_safe
            and gof_improvement is not None
            and gof_improvement >= minimum_relative_gof_improvement
        )
        if not numerical_safe:
            review_flags.append(f"preferred_orientation_trial_unsafe:{phase}")
        elif material:
            review_flags.append(f"preferred_orientation_model_material:{phase}")
        if (
            maximum_fraction_change is not None
            and maximum_fraction_change > fraction_spread_review_limit
        ):
            review_flags.append(f"preferred_orientation_fraction_spread:{phase}")
        rows.append(
            {
                "phase": phase,
                "axis": trial.get("preferred_orientation", {}).get("axis"),
                "ratio": ratio,
                "ratio_esd": ratio_esd,
                "numerically_safe": numerical_safe,
                "reasons": reasons,
                "relative_gof_improvement": gof_improvement,
                "material_gof_improvement": material,
                "maximum_sample_fraction_change": maximum_fraction_change,
                "candidate": trial.get("candidate"),
            }
        )
    review_flags = list(dict.fromkeys(review_flags))
    return {
        "status": "review" if review_flags else "pass",
        "hard_failures": [],
        "review_flags": review_flags,
        "automatic_promotion": False,
        "selection_note": (
            "A March-Dollase sensitivity model is never promoted solely by lower GOF; "
            "a material safe branch requires an explicitly declared primary rerun."
        ),
        "ratio_bounds": list(ratio_bounds),
        "shift_limit": shift_limit,
        "minimum_relative_gof_improvement": minimum_relative_gof_improvement,
        "fraction_spread_review_limit": fraction_spread_review_limit,
        "phase_fraction_spread": phase_spread,
        "trials": rows,
    }


def microabsorption_multiplier_sensitivity(
    fractions: dict[str, float],
    multiplier_intervals: dict[str, tuple[float, float]],
    *,
    spread_review_limit: float = 0.01,
) -> dict[str, Any]:
    """Propagate externally justified true-mass multiplier intervals.

    ``multiplier`` is deliberately defined as a direct multiplier on the
    Rietveld-derived crystalline mass contribution. This avoids silently
    assuming a Brindley particle geometry, absorption coefficient convention
    or unit system that GSAS-II did not refine.
    """
    if not fractions or set(fractions) != set(multiplier_intervals):
        raise ValueError("microabsorption multipliers must cover every quantitative phase")
    if spread_review_limit <= 0.0:
        raise ValueError("microabsorption spread review limit must be positive")
    values = {name: float(value) for name, value in fractions.items()}
    if any(not math.isfinite(value) or value < 0.0 for value in values.values()):
        raise ValueError("mass fractions must be finite and nonnegative")
    total = sum(values.values())
    if total <= 0.0:
        raise ValueError("mass fractions must have a positive sum")
    values = {name: value / total for name, value in values.items()}
    intervals = {}
    for name, interval in multiplier_intervals.items():
        low, high = (float(item) for item in interval)
        if not all(math.isfinite(item) and item > 0.0 for item in (low, high)) or low > high:
            raise ValueError(f"invalid microabsorption multiplier interval for {name}")
        intervals[name] = (low, high)
    midpoints = {name: 0.5 * (low + high) for name, (low, high) in intervals.items()}

    def corrected(multipliers: dict[str, float]) -> dict[str, float]:
        weighted = {name: values[name] * multipliers[name] for name in values}
        denominator = sum(weighted.values())
        if denominator <= 0.0:
            raise ValueError("corrected microabsorption contribution sum is not positive")
        return {name: weighted[name] / denominator for name in values}

    midpoint = corrected(midpoints)
    phase_rows = {}
    review_flags = []
    for name in values:
        low_case = {
            other: intervals[other][0] if other == name else intervals[other][1]
            for other in values
        }
        high_case = {
            other: intervals[other][1] if other == name else intervals[other][0]
            for other in values
        }
        lower = corrected(low_case)[name]
        upper = corrected(high_case)[name]
        spread = upper - lower
        half_range = 0.5 * spread
        maximum_shift = max(abs(lower - values[name]), abs(upper - values[name]))
        if maximum_shift > spread_review_limit:
            review_flags.append(f"microabsorption_fraction_sensitivity:{name}")
        phase_rows[name] = {
            "uncorrected": values[name],
            "midpoint_corrected": midpoint[name],
            "minimum": lower,
            "maximum": upper,
            "interval_width": spread,
            "half_range": half_range,
            "maximum_absolute_shift": maximum_shift,
            "multiplier_interval": list(intervals[name]),
        }
    review_flags = list(dict.fromkeys(review_flags))
    return {
        "status": "review" if review_flags else "pass",
        "hard_failures": [],
        "review_flags": review_flags,
        "multiplier_definition": MICROABSORPTION_MULTIPLIER_DEFINITION,
        "automatic_primary_correction": False,
        "spread_review_limit": spread_review_limit,
        "phases": phase_rows,
    }


def amorphous_from_internal_standard(
    *,
    added_standard_fraction: float,
    refined_standard_fraction: float,
    refined_standard_esd: float | None,
    added_standard_fraction_esd: float | None,
) -> dict[str, Any]:
    """Calculate original-sample amorphous fraction by known standard addition.

    The known standard fraction is the standard mass divided by the total mass
    after addition. The refined standard fraction is normalized over all
    crystalline sample phases plus the standard, excluding hardware phases.
    """
    known = float(added_standard_fraction)
    refined = float(refined_standard_fraction)
    if not 0.0 < known < 1.0:
        raise ValueError("added internal-standard fraction must be between zero and one")
    if not 0.0 < refined < 1.0:
        raise ValueError("refined internal-standard fraction must be between zero and one")
    amorphous = (1.0 - known / refined) / (1.0 - known)
    hard_failures = []
    review_flags = []
    if amorphous < -1e-8 or amorphous > 1.0 + 1e-8:
        hard_failures.append("nonphysical_internal_standard_amorphous_fraction")
    if abs(amorphous) <= 1e-8:
        amorphous = 0.0
    refined_esd = None if refined_standard_esd is None else float(refined_standard_esd)
    known_esd = (
        None
        if added_standard_fraction_esd is None
        else float(added_standard_fraction_esd)
    )
    if refined_esd is None or not math.isfinite(refined_esd) or refined_esd <= 0.0:
        review_flags.append("internal_standard_refined_uncertainty_missing")
    if known_esd is None or not math.isfinite(known_esd) or known_esd <= 0.0:
        review_flags.append("internal_standard_addition_uncertainty_missing")
    esd = None
    if (
        refined_esd is not None
        and math.isfinite(refined_esd)
        and refined_esd > 0.0
        and known_esd is not None
        and math.isfinite(known_esd)
        and known_esd > 0.0
    ):
        derivative_refined = known / ((1.0 - known) * refined**2)
        derivative_known = -(1.0 - refined) / (refined * (1.0 - known) ** 2)
        esd = math.sqrt(
            (derivative_refined * refined_esd) ** 2
            + (derivative_known * known_esd) ** 2
        )
    return {
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "hard_failures": hard_failures,
        "review_flags": review_flags,
        "basis": "original sample before internal-standard addition",
        "formula": "A = (1 - W_added / R_refined) / (1 - W_added)",
        "added_standard_fraction_after_mixing": known,
        "added_standard_fraction_esd": known_esd,
        "refined_standard_fraction_of_crystalline_plus_standard": refined,
        "refined_standard_fraction_esd": refined_esd,
        "amorphous_fraction": amorphous,
        "amorphous_fraction_esd": esd,
    }


def amorphous_interval_from_internal_standard(
    *,
    added_standard_fraction: float,
    refined_standard_fraction_interval: tuple[float, float],
) -> dict[str, Any]:
    """Propagate a model-sensitivity interval in refined standard fraction."""
    known = float(added_standard_fraction)
    low_refined, high_refined = (
        float(item) for item in refined_standard_fraction_interval
    )
    if not 0.0 < known < 1.0:
        raise ValueError("added internal-standard fraction must be between zero and one")
    if not 0.0 < low_refined <= high_refined < 1.0:
        raise ValueError("refined internal-standard interval must lie within zero and one")

    def convert(refined: float) -> float:
        return (1.0 - known / refined) / (1.0 - known)

    values = [convert(low_refined), convert(high_refined)]
    minimum, maximum = min(values), max(values)
    review_flags = []
    if minimum < 0.0 or maximum > 1.0:
        review_flags.append("model_sensitivity_amorphous_interval_nonphysical")
    return {
        "status": "review" if review_flags else "pass",
        "hard_failures": [],
        "review_flags": review_flags,
        "minimum": minimum,
        "maximum": maximum,
        "half_range": 0.5 * (maximum - minimum),
        "refined_standard_fraction_interval": [low_refined, high_refined],
    }


def assess_trace_phases(
    fractions: dict[str, dict[str, Any]],
    uncertainties: dict[str, dict[str, Any]],
    *,
    trace_fraction_threshold: float = 0.05,
    detection_sigma: float = 3.0,
    quantification_sigma: float = 10.0,
) -> dict[str, Any]:
    """Classify trace phases using reported uncertainty without inventing LODs."""
    if not 0.0 < trace_fraction_threshold < 1.0:
        raise ValueError("trace fraction threshold must be between zero and one")
    if not 0.0 < detection_sigma < quantification_sigma:
        raise ValueError("trace sigma thresholds must satisfy 0 < detection < quantification")
    review_flags = []
    rows = {}
    for name, item in fractions.items():
        value = float(item["value"])
        if value > trace_fraction_threshold:
            continue
        uncertainty_record = uncertainties.get(name, {})
        sigma = uncertainty_record.get("conservative_combined")
        uncertainty_basis = "conservative_combined"
        if sigma is None:
            sigma = uncertainty_record.get("available_components_combined")
            uncertainty_basis = "available_components_combined_incomplete"
        if sigma is None:
            sigma = item.get("esd")
            uncertainty_basis = "covariance_esd_only"
        if sigma is None or not math.isfinite(float(sigma)) or float(sigma) <= 0.0:
            classification = "uncertainty_unavailable"
            signal_to_uncertainty = None
            upper_limit = None
            review_flags.append(f"trace_phase_uncertainty_unavailable:{name}")
        else:
            sigma = float(sigma)
            signal_to_uncertainty = value / sigma
            upper_limit = detection_sigma * sigma
            if signal_to_uncertainty < detection_sigma:
                classification = "not_detected_statistically"
                review_flags.append(f"trace_phase_below_detection:{name}")
            elif signal_to_uncertainty < quantification_sigma:
                classification = "detected_not_quantifiable"
                review_flags.append(f"trace_phase_below_quantification:{name}")
            else:
                classification = "quantified_statistically"
            if uncertainty_basis != "conservative_combined":
                review_flags.append(f"trace_phase_model_uncertainty_incomplete:{name}")
        rows[name] = {
            "fraction": value,
            "classification": classification,
            "uncertainty": sigma,
            "uncertainty_basis": uncertainty_basis,
            "signal_to_uncertainty": signal_to_uncertainty,
            "statistical_detection_upper_limit": upper_limit,
        }
    review_flags = list(dict.fromkeys(review_flags))
    return {
        "status": "review" if review_flags else "pass",
        "hard_failures": [],
        "review_flags": review_flags,
        "trace_fraction_threshold": trace_fraction_threshold,
        "detection_sigma": detection_sigma,
        "quantification_sigma": quantification_sigma,
        "scope_note": (
            "This is a covariance/model-uncertainty classification, not a validated "
            "instrumental LOD/LOQ; spike-in or profile-likelihood validation is still required."
        ),
        "phases": rows,
    }


def select_constrained_model_variant(
    variants: list[dict[str, Any]],
    *,
    relative_gof_tolerance: float = 1e-5,
) -> dict[str, Any]:
    """Rank frozen CIF variants without treating occupancy as a free parameter."""
    if not variants:
        raise ValueError("at least one constrained model variant is required")
    if relative_gof_tolerance < 0.0:
        raise ValueError("relative GOF tolerance must be nonnegative")
    labels = [item.get("label") for item in variants]
    if any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("every constrained model variant requires a nonblank label")
    if len(set(labels)) != len(labels):
        raise ValueError("constrained model variant labels must be unique")
    for item in variants:
        assessment = item.get("scientific_assessment")
        if not isinstance(assessment, dict) or not isinstance(
            assessment.get("hard_failures"), list
        ):
            raise ValueError(
                f"constrained model variant lacks audited hard failures: {item['label']}"
            )

    def hard_count(item: dict[str, Any]) -> int:
        assessment = item["scientific_assessment"]
        return len(set(assessment.get("hard_failures", [])))

    minimum_hard = min(hard_count(item) for item in variants)
    viable = [item for item in variants if hard_count(item) == minimum_hard]
    finite = [
        item
        for item in viable
        if item.get("metrics", {}).get("GOF") is not None
        and math.isfinite(float(item["metrics"]["GOF"]))
    ]
    if not finite:
        selected = sorted(viable, key=lambda item: str(item.get("label", "")))[0]
        competitive = viable
    else:
        best_gof = min(float(item["metrics"]["GOF"]) for item in finite)
        threshold = best_gof * (1.0 + relative_gof_tolerance)
        competitive = [item for item in finite if float(item["metrics"]["GOF"]) <= threshold]

        def conditioning_key(item: dict[str, Any]) -> tuple[float, str]:
            correlation = item.get("metrics", {}).get("maximum_correlation", {}).get("absolute")
            return (
                float(correlation)
                if correlation is not None and math.isfinite(float(correlation))
                else math.inf,
                str(item.get("label", "")),
            )

        selected = min(competitive, key=conditioning_key)
    hard_failures = []
    review_flags = ["dopant_site_or_composition_not_established_by_xrd_grid"]
    if len(competitive) > 1:
        review_flags.append("constrained_model_variants_indistinguishable")
    if minimum_hard:
        hard_failures.append("all_constrained_model_variants_have_hard_failures")
    return {
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "hard_failures": hard_failures,
        "review_flags": review_flags,
        "selected_label": selected.get("label"),
        "competitive_labels": [item.get("label") for item in competitive],
        "minimum_hard_failure_count": minimum_hard,
        "selection_policy": (
            "minimum hard failures, then GOF-competitive set, then lowest correlation; "
            "never infer dopant content or site occupancy from a single lower-Rwp path"
        ),
    }
