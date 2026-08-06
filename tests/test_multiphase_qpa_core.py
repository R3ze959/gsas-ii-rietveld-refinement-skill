import importlib.util
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[1]
    / "gsas-ii-multiphase-refinement"
    / "scripts"
    / "qpa_core.py"
)
SPEC = importlib.util.spec_from_file_location("qpa_core", MODULE)
qpa_core = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(qpa_core)


def test_mass_fraction_scale_round_trip():
    mass_a = 1852.3
    mass_b = 318.2
    for target_b in (0.01, 0.10, 0.50, 0.90):
        scale_b = qpa_core.phase_b_scale_for_mass_fraction(
            target_b, mass_a, mass_b
        )
        fraction_a, fraction_b = qpa_core.mass_fractions_from_scales(
            1.0, scale_b, mass_a, mass_b
        )
        assert abs(fraction_b - target_b) < 1e-12
        assert abs(fraction_a - (1.0 - target_b)) < 1e-12


def test_mass_normalized_scales_keep_specimen_basis_constant():
    mass_a = 1852.3
    mass_b = 318.2
    basis = mass_a
    totals = []
    for target_b in (0.01, 0.10, 0.50, 0.90):
        scale_a, scale_b = qpa_core.mass_normalized_scales(
            target_b, mass_a, mass_b, basis
        )
        fraction_a, fraction_b = qpa_core.mass_fractions_from_scales(
            scale_a, scale_b, mass_a, mass_b
        )
        totals.append(scale_a * mass_a + scale_b * mass_b)
        assert abs(fraction_b - target_b) < 1e-12
        assert abs(fraction_a - (1.0 - target_b)) < 1e-12
    assert max(totals) - min(totals) < 1e-10


def test_n_phase_starting_scales_recover_requested_fractions():
    fractions = {"A": 0.1, "B": 0.2, "C": 0.7}
    masses = {"A": 100.0, "B": 250.0, "C": 80.0}
    scales = qpa_core.scales_for_mass_fractions(fractions, masses, "B")
    weighted = {name: scales[name] * masses[name] for name in fractions}
    total = sum(weighted.values())
    assert scales["B"] == 1.0
    for name, target in fractions.items():
        assert abs(weighted[name] / total - target) < 1e-12


def test_real_qpa_gate_separates_modeled_and_unexplained_residuals():
    candidate = {
        "metrics": {
            "converged": True,
            "svd_count": 0,
            "max_shift_over_esd": 0.001,
            "maximum_correlation": {"absolute": 0.8},
        },
        "refined_mass_fractions": {
            "A": {"value": 0.6, "esd": 0.01},
            "B": {"value": 0.4, "esd": 0.01},
        },
        "residual_audit": {
            "positive_local_maxima": [
                {
                    "two_theta": 30.0,
                    "robust_sigma_above_residual": 8.0,
                    "residual_percent_of_pattern_maximum": 3.0,
                    "modeled_profile_percent_of_pattern_maximum": 5.0,
                }
            ]
        },
    }
    review = qpa_core.assess_real_qpa_candidate(candidate)
    assert review["status"] == "review"
    assert "modeled_peak_residual:30.0000deg" in review["review_flags"]
    candidate["residual_audit"]["positive_local_maxima"][0].update(
        {
            "residual_percent_of_pattern_maximum": 12.0,
            "modeled_profile_percent_of_pattern_maximum": 0.0,
        }
    )
    failed = qpa_core.assess_real_qpa_candidate(candidate)
    assert failed["status"] == "fail"
    assert "major_profile_or_phase_model_residual:30.0000deg" in failed["hard_failures"]


def test_candidate_selection_does_not_require_target():
    common = {
        "refined_mass_fractions": {"A": {"value": 0.7}, "B": {"value": 0.3}},
        "metrics": {
            "converged": True,
            "svd_count": 0,
            "max_shift_over_esd": 0.2,
            "maximum_correlation": {"absolute": 0.5},
        },
    }
    worse = {**common, "candidate": "worse", "metrics": {**common["metrics"], "GOF": 1.2}}
    better = {**common, "candidate": "better", "metrics": {**common["metrics"], "GOF": 1.1}}
    assert qpa_core.candidate_selection_key(better) < qpa_core.candidate_selection_key(worse)

    equivalent_better_conditioned = {
        **common,
        "candidate": "conditioned",
        "metrics": {
            **common["metrics"],
            "GOF": 1.1 + 1e-12,
            "maximum_correlation": {"absolute": 0.2},
        },
    }
    selected, competitive = qpa_core.select_competitive_candidate(
        [better, equivalent_better_conditioned]
    )
    assert len(competitive) == 2
    assert selected["candidate"] == "conditioned"


def test_real_qpa_competitive_window_rejects_trace_anchor_degeneracy():
    """A negligible GOF gain must not hide a much worse scale correlation."""
    abundant_anchor = {
        "candidate": "abundant_anchor",
        "refined_mass_fractions": {
            "major": {"value": 0.94},
            "minor_a": {"value": 0.045},
            "minor_b": {"value": 0.015},
        },
        "metrics": {
            "converged": True,
            "svd_count": 0,
            "GOF": 2.6992376,
            "max_shift_over_esd": 0.01,
            "maximum_correlation": {"absolute": 0.73},
        },
    }
    trace_anchor = {
        **abundant_anchor,
        "candidate": "trace_anchor",
        "metrics": {
            **abundant_anchor["metrics"],
            "GOF": 2.6992323,
            "maximum_correlation": {"absolute": 0.99},
        },
    }
    selected, competitive = qpa_core.select_competitive_candidate(
        [trace_anchor, abundant_anchor], relative_gof_tolerance=1e-5
    )
    assert len(competitive) == 2
    assert selected["candidate"] == "abundant_anchor"


def test_candidate_selection_includes_scientific_hard_failures():
    unsafe_low_gof = {
        "candidate": "unsafe_low_gof",
        "assessment": {"hard_failures": ["major_profile_or_phase_model_residual:30deg"]},
        "refined_mass_fractions": {"A": {"value": 0.5}, "B": {"value": 0.5}},
        "metrics": {
            "converged": True,
            "svd_count": 0,
            "GOF": 1.0,
            "max_shift_over_esd": 0.01,
            "maximum_correlation": {"absolute": 0.2},
        },
    }
    safe_higher_gof = {
        **unsafe_low_gof,
        "candidate": "safe_higher_gof",
        "assessment": {"hard_failures": []},
        "metrics": {**unsafe_low_gof["metrics"], "GOF": 1.2},
    }
    selected, _ = qpa_core.select_competitive_candidate(
        [unsafe_low_gof, safe_higher_gof], relative_gof_tolerance=1e-5
    )
    assert selected["candidate"] == "safe_higher_gof"


def test_phase_model_import_audit_detects_wrong_gsasii_setting_mass():
    result = qpa_core.assess_phase_model_import(
        source_composition={"Ca": 8.0, "Fe": 4.0, "Al": 4.0, "O": 20.0},
        imported_composition={"Ca": 8.0, "Fe": 4.96, "Al": 7.04, "O": 24.0},
        expected_cell_mass=971.936,
        imported_cell_mass=1171.5704,
        import_log="space group setting not compatible with GSAS-II",
    )
    assert result["status"] == "fail"
    assert "imported_unit_cell_composition_mismatch" in result["hard_failures"]
    assert "imported_unit_cell_mass_mismatch" in result["hard_failures"]
    assert "gsasii_incompatible_space_group_setting" in result["hard_failures"]


def test_phase_set_gate_blocks_unknown_and_held_out_provisional_models():
    unknown = qpa_core.phase_set_completeness_gate(
        "unknown", evidence=None, held_out=False
    )
    assert unknown["status"] == "fail"
    provisional_blind = qpa_core.phase_set_completeness_gate(
        "provisional", evidence="whole-pattern screen", held_out=True
    )
    assert provisional_blind["status"] == "fail"
    verified = qpa_core.phase_set_completeness_gate(
        "verified", evidence="independent phase-identification report", held_out=True
    )
    assert verified["status"] == "pass"


def test_repeatability_audit_rejects_phase_allocation_instability():
    records = [
        {
            "status": "review",
            "refined_mass_fractions": {
                "A": {"value": 0.60},
                "B": {"value": 0.40},
            },
        },
        {
            "status": "review",
            "refined_mass_fractions": {
                "A": {"value": 0.61},
                "B": {"value": 0.39},
            },
        },
        {
            "status": "review",
            "refined_mass_fractions": {
                "A": {"value": 0.602},
                "B": {"value": 0.398},
            },
        },
    ]
    result = qpa_core.summarize_qpa_repeatability(
        records, ["A", "B"], fraction_range_limit=0.005
    )
    assert result["status"] == "fail"
    assert abs(result["phases"]["A"]["range"] - 0.01) < 1e-12


def test_role_normalized_mass_fraction_propagates_scale_covariance():
    result = qpa_core.normalized_mass_fractions_with_covariance(
        scales={"A": 1.0, "B": 1.0},
        masses={"A": 100.0, "B": 100.0},
        varying_scale_indices={"B": 0},
        covariance=[[0.04]],
    )
    assert result["A"]["value"] == 0.5
    assert result["B"]["value"] == 0.5
    assert abs(result["A"]["esd"] - 0.05) < 1e-12
    assert abs(result["B"]["esd"] - 0.05) < 1e-12


def test_repeatability_can_audit_sample_role_normalization():
    records = [
        {"sample_normalized_mass_fractions": {"A": {"value": 0.7}}},
        {"sample_normalized_mass_fractions": {"A": {"value": 0.701}}},
    ]
    result = qpa_core.summarize_qpa_repeatability(
        records,
        ["A"],
        fraction_range_limit=0.005,
        fraction_key="sample_normalized_mass_fractions",
    )
    assert result["status"] == "pass"


def test_replicate_summary_reports_bias_rmse_and_coverage():
    summary = qpa_core.summarize_replicates(
        [
            {"status": "pass", "selected_fraction": 0.10, "selected_esd": 0.01, "selected_anchor": "A"},
            {"status": "review", "selected_fraction": 0.12, "selected_esd": 0.01, "selected_anchor": "B"},
        ],
        nominal_fraction=0.10,
    )
    assert abs(summary["bias"] - 0.01) < 1e-12
    assert abs(summary["rmse"] - (0.0002 ** 0.5)) < 1e-12
    assert summary["coverage_1sigma"] == 0.5
    assert summary["coverage_2sigma"] == 1.0


def test_case_gate_distinguishes_review_and_fail():
    passing = qpa_core.evaluate_training_case(
        target_fraction=0.1,
        refined_fraction=0.1005,
        refined_esd=0.0002,
        converged=True,
        svd_count=0,
        max_shift_over_esd=0.5,
        max_correlation=0.8,
        absolute_error_limit=0.002,
        correlation_limit=0.95,
        shift_limit=1.0,
    )
    assert passing["status"] == "pass"

    review = dict(passing)
    review = qpa_core.evaluate_training_case(
        target_fraction=0.1,
        refined_fraction=0.104,
        refined_esd=0.0002,
        converged=True,
        svd_count=0,
        max_shift_over_esd=0.5,
        max_correlation=0.8,
        absolute_error_limit=0.002,
        correlation_limit=0.95,
        shift_limit=1.0,
    )
    assert review["status"] == "review"

    failed = qpa_core.evaluate_training_case(
        target_fraction=0.1,
        refined_fraction=0.1,
        refined_esd=0.0002,
        converged=False,
        svd_count=1,
        max_shift_over_esd=0.5,
        max_correlation=0.8,
        absolute_error_limit=0.002,
        correlation_limit=0.95,
        shift_limit=1.0,
    )
    assert failed["status"] == "fail"
