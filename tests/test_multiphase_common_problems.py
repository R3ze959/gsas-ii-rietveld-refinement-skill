import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path


MODULE = (
    Path(__file__).resolve().parents[1]
    / "gsas-ii-multiphase-refinement"
    / "scripts"
    / "qpa_common_problems.py"
)
SPEC = importlib.util.spec_from_file_location("qpa_common_problems", MODULE)
common = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(common)


def test_parse_preferred_orientation_axis_and_multiplier_interval():
    assert common.parse_hkl("0, 0, 1") == (0, 0, 1)
    assert common.parse_positive_interval("0.95,1.10") == (0.95, 1.10)
    for invalid in ("0,0", "0,0,0", "a,0,1"):
        try:
            common.parse_hkl(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid axis was accepted: {invalid}")


def test_common_problem_evidence_must_match_declared_branch_exactly():
    po_evidence = {
        "schema_version": 1,
        "kind": "preferred_orientation_assessment",
        "status": "pass",
        "basis": "plate-like morphology and declared cleavage axis",
        "policy": "sensitivity",
        "conclusion": "sensitivity_defined",
        "phase_axes": {"A": [0, 0, 1]},
    }
    result = common.validate_common_problem_evidence(
        po_evidence,
        kind="preferred_orientation_assessment",
        policy="sensitivity",
        expected_phase_axes={"A": (0, 0, 1)},
    )
    assert result["phase_axes"] == {"A": [0, 0, 1]}

    po_evidence["phase_axes"] = {"A": [1, 0, 0]}
    try:
        common.validate_common_problem_evidence(
            po_evidence,
            kind="preferred_orientation_assessment",
            policy="sensitivity",
            expected_phase_axes={"A": (0, 0, 1)},
        )
    except ValueError as exc:
        assert "do not match" in str(exc)
    else:
        raise AssertionError("mismatched preferred-orientation evidence was accepted")


def test_common_problem_evidence_rejects_disabled_scientific_claim():
    evidence = {
        "schema_version": 1,
        "kind": "microabsorption_assessment",
        "status": "pass",
        "scientific_claim": False,
        "basis": "development placeholder",
        "policy": "assessed_negligible",
        "conclusion": "negligible",
    }
    try:
        common.validate_common_problem_evidence(
            evidence,
            kind="microabsorption_assessment",
            policy="assessed_negligible",
        )
    except ValueError as exc:
        assert "disables scientific use" in str(exc)
    else:
        raise AssertionError("scientific_claim=false evidence was accepted")


def test_microabsorption_and_internal_standard_evidence_match_numbers():
    micro = {
        "schema_version": 1,
        "kind": "microabsorption_assessment",
        "status": "pass",
        "basis": "absorption and particle-size bounds",
        "policy": "sensitivity",
        "conclusion": "sensitivity_defined",
        "multiplier_definition": common.MICROABSORPTION_MULTIPLIER_DEFINITION,
        "phase_intervals": {"A": [0.95, 1.05], "standard": [1.0, 1.0]},
    }
    validated = common.validate_common_problem_evidence(
        micro,
        kind="microabsorption_assessment",
        policy="sensitivity",
        expected_phase_intervals={"A": (0.95, 1.05), "standard": (1.0, 1.0)},
    )
    assert validated["phase_intervals"]["A"] == [0.95, 1.05]

    addition = {
        "schema_version": 1,
        "kind": "internal_standard_addition",
        "status": "pass",
        "basis": "post_addition_total_mass",
        "conclusion": "addition_record_verified",
        "standard_phase": "standard",
        "added_fraction_after_mixing": 0.20,
        "added_fraction_esd": 0.001,
    }
    validated = common.validate_common_problem_evidence(
        addition,
        kind="internal_standard_addition",
        expected_standard_phase="standard",
        expected_added_fraction=0.20,
        expected_added_fraction_esd=0.001,
    )
    assert validated["added_fraction_after_mixing"] == 0.20
    addition["added_fraction_esd"] = 0.002
    try:
        common.validate_common_problem_evidence(
            addition,
            kind="internal_standard_addition",
            expected_standard_phase="standard",
            expected_added_fraction=0.20,
            expected_added_fraction_esd=0.001,
        )
    except ValueError as exc:
        assert "fraction ESD" in str(exc)
    else:
        raise AssertionError("mismatched internal-standard ESD was accepted")


def test_internal_standard_amorphous_formula_and_uncertainty():
    # 20 wt% standard was added after mixing. If refinement reports the
    # standard as 25% of all crystalline matter, the original sample is 25%
    # amorphous: (1 - 0.2/0.25) / (1 - 0.2) = 0.25.
    result = common.amorphous_from_internal_standard(
        added_standard_fraction=0.20,
        refined_standard_fraction=0.25,
        refined_standard_esd=0.002,
        added_standard_fraction_esd=0.001,
    )
    assert result["status"] == "pass"
    assert abs(result["amorphous_fraction"] - 0.25) < 1e-12
    expected_esd = math.sqrt(
        (0.20 / (0.80 * 0.25**2) * 0.002) ** 2
        + ((1.0 - 0.25) / (0.25 * 0.80**2) * 0.001) ** 2
    )
    assert abs(result["amorphous_fraction_esd"] - expected_esd) < 1e-12


def test_internal_standard_rejects_nonphysical_negative_amorphous_result():
    result = common.amorphous_from_internal_standard(
        added_standard_fraction=0.20,
        refined_standard_fraction=0.18,
        refined_standard_esd=0.002,
        added_standard_fraction_esd=0.001,
    )
    assert result["status"] == "fail"
    assert "nonphysical_internal_standard_amorphous_fraction" in result["hard_failures"]


def test_internal_standard_propagates_microabsorption_model_interval():
    result = common.amorphous_interval_from_internal_standard(
        added_standard_fraction=0.20,
        refined_standard_fraction_interval=(0.24, 0.26),
    )
    assert result["status"] == "pass"
    assert result["minimum"] < 0.25 < result["maximum"]
    assert abs(result["half_range"] - 0.5 * (result["maximum"] - result["minimum"])) < 1e-12


def test_microabsorption_sensitivity_uses_complete_phase_intervals():
    result = common.microabsorption_multiplier_sensitivity(
        {"A": 0.6, "B": 0.3, "standard": 0.1},
        {
            "A": (0.9, 1.1),
            "B": (0.95, 1.05),
            "standard": (1.0, 1.0),
        },
        spread_review_limit=0.005,
    )
    assert result["status"] == "review"
    assert result["automatic_primary_correction"] is False
    for name, row in result["phases"].items():
        assert row["minimum"] <= row["midpoint_corrected"] <= row["maximum"]
        assert row["minimum"] <= {"A": 0.6, "B": 0.3, "standard": 0.1}[name] <= row["maximum"]
    assert abs(
        sum(row["midpoint_corrected"] for row in result["phases"].values()) - 1.0
    ) < 1e-12


def test_trace_phase_classification_uses_conservative_uncertainty_when_available():
    fractions = {
        "major": {"value": 0.97, "esd": 0.001},
        "detected": {"value": 0.02, "esd": 0.001},
        "not_detected": {"value": 0.01, "esd": 0.001},
    }
    uncertainties = {
        "detected": {
            "conservative_combined": 0.003,
            "available_components_combined": 0.002,
        },
        "not_detected": {
            "conservative_combined": 0.005,
            "available_components_combined": 0.004,
        },
    }
    result = common.assess_trace_phases(fractions, uncertainties)
    assert result["status"] == "review"
    assert result["phases"]["detected"]["classification"] == "detected_not_quantifiable"
    assert result["phases"]["not_detected"]["classification"] == "not_detected_statistically"
    assert "major" not in result["phases"]


def _po_candidate(
    phase: str,
    *,
    gof: float,
    correlation: float,
    ratio: float,
    fractions: tuple[float, float],
) -> dict:
    return {
        "candidate": f"po_{phase}",
        "metrics": {
            "GOF": gof,
            "converged": True,
            "svd_count": 0,
            "max_shift_over_esd": 0.001,
            "maximum_correlation": {"absolute": correlation},
        },
        "preferred_orientation": {
            "phase": phase,
            "axis": [0, 0, 1],
            "ratio": ratio,
            "esd": 0.02,
        },
        "sample_normalized_mass_fractions": {
            "A": {"value": fractions[0], "esd": 0.002},
            "B": {"value": fractions[1], "esd": 0.002},
        },
    }


def test_preferred_orientation_sensitivity_never_auto_promotes_material_model():
    baseline = {
        "metrics": {"GOF": 2.0},
        "sample_normalized_mass_fractions": {
            "A": {"value": 0.7, "esd": 0.002},
            "B": {"value": 0.3, "esd": 0.002},
        },
    }
    result = common.assess_preferred_orientation_sensitivity(
        baseline,
        [
            _po_candidate(
                "A", gof=1.95, correlation=0.80, ratio=0.75, fractions=(0.68, 0.32)
            )
        ],
        minimum_relative_gof_improvement=0.005,
        fraction_spread_review_limit=0.01,
    )
    assert result["status"] == "review"
    assert result["automatic_promotion"] is False
    assert "preferred_orientation_model_material:A" in result["review_flags"]
    assert "preferred_orientation_fraction_spread:A" in result["review_flags"]


def test_preferred_orientation_sensitivity_can_clear_when_effect_is_immaterial():
    baseline = {
        "metrics": {"GOF": 2.0},
        "sample_normalized_mass_fractions": {
            "A": {"value": 0.7, "esd": 0.002},
            "B": {"value": 0.3, "esd": 0.002},
        },
    }
    result = common.assess_preferred_orientation_sensitivity(
        baseline,
        [
            _po_candidate(
                "A", gof=1.9995, correlation=0.80, ratio=0.99, fractions=(0.7005, 0.2995)
            )
        ],
    )
    assert result["status"] == "pass"
    assert result["review_flags"] == []


def test_combined_common_problem_assessment_preserves_fail_precedence():
    amorphous = common.amorphous_from_internal_standard(
        added_standard_fraction=0.20,
        refined_standard_fraction=0.18,
        refined_standard_esd=0.002,
        added_standard_fraction_esd=0.001,
    )
    micro = common.microabsorption_multiplier_sensitivity(
        {"A": 0.8, "standard": 0.2},
        {"A": (0.99, 1.01), "standard": (1.0, 1.0)},
        spread_review_limit=0.02,
    )
    trace = common.assess_trace_phases(
        {"A": {"value": 0.98, "esd": 0.001}, "trace": {"value": 0.02, "esd": 0.01}},
        {"trace": {"conservative_combined": 0.01}},
    )
    combined = common.merge_assessments(amorphous, micro, trace)
    assert combined["status"] == "fail"
    assert "nonphysical_internal_standard_amorphous_fraction" in combined["hard_failures"]
    assert "trace_phase_below_detection:trace" in combined["review_flags"]


def test_constrained_variant_grid_uses_hard_failures_then_conditioning():
    variants = [
        {
            "label": "x0.10_site_a",
            "scientific_assessment": {"hard_failures": []},
            "metrics": {"GOF": 1.500000, "maximum_correlation": {"absolute": 0.80}},
        },
        {
            "label": "x0.10_site_b",
            "scientific_assessment": {"hard_failures": []},
            "metrics": {"GOF": 1.500005, "maximum_correlation": {"absolute": 0.60}},
        },
        {
            "label": "x0.20_site_a",
            "scientific_assessment": {"hard_failures": ["major_residual"]},
            "metrics": {"GOF": 1.20, "maximum_correlation": {"absolute": 0.20}},
        },
    ]
    result = common.select_constrained_model_variant(
        variants, relative_gof_tolerance=1e-5
    )
    assert result["selected_label"] == "x0.10_site_b"
    assert result["status"] == "review"
    assert "dopant_site_or_composition_not_established_by_xrd_grid" in result["review_flags"]
    assert "x0.20_site_a" not in result["competitive_labels"]


def test_constrained_variant_grid_fails_when_every_model_has_hard_failures():
    result = common.select_constrained_model_variant(
        [
            {
                "label": "site_a",
                "scientific_assessment": {"hard_failures": ["major_residual"]},
                "metrics": {"GOF": 1.2, "maximum_correlation": {"absolute": 0.4}},
            },
            {
                "label": "site_b",
                "scientific_assessment": {"hard_failures": ["nonconverged"]},
                "metrics": {"GOF": 1.3, "maximum_correlation": {"absolute": 0.3}},
            },
        ]
    )
    assert result["status"] == "fail"
    assert "all_constrained_model_variants_have_hard_failures" in result["hard_failures"]


def _write_variant(tmp_path: Path, label: str, target_hash: str, gof: float, correlation: float) -> Path:
    variant_dir = tmp_path / label
    variant_dir.mkdir()
    protocol = {
        "schema_version": 4,
        "answer_status_at_freeze": "not_applicable",
        "answer_values_present": False,
        "inputs": {
            "pattern": {"sha256": "pattern-hash"},
            "instrument": {"sha256": "instrument-hash"},
            "phases": {
                "doped": {"path": f"{label}.cif", "sha256": target_hash},
                "impurity": {"path": "impurity.cif", "sha256": "impurity-hash"},
            },
        },
        "settings": {"background_order": 10, "phase_roles": {"doped": "sample", "impurity": "sample"}},
    }
    protocol_path = variant_dir / "protocol_manifest.json"
    protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
    summary = {
        "schema_version": 4,
        "real_gsasii": True,
        "status": "review",
        "protocol_manifest": str(protocol_path),
        "phase_model_audit": {"status": "pass"},
        "scientific_assessment": {
            "status": "review",
            "hard_failures": [],
            "review_flags": ["modeled_peak_residual"],
        },
        "selected_result": {
            "metrics": {"GOF": gof, "maximum_correlation": {"absolute": correlation}},
            "sample_normalized_mass_fractions": {
                "doped": {"value": 0.9, "esd": 0.01},
                "impurity": {"value": 0.1, "esd": 0.01},
            },
        },
    }
    summary_path = variant_dir / "qpa_summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    return summary_path


def test_constrained_model_grid_script_checks_invariants_and_writes_archive(tmp_path):
    evidence = tmp_path / "composition-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "constrained_model_grid",
                "status": "pass",
                "basis": "predeclared composition and site hypotheses",
                "target_phase": "doped",
                "variant_labels": ["site_a", "site_b"],
                "claim_scope": "model_comparison_only",
            }
        ),
        encoding="utf-8",
    )
    first = _write_variant(tmp_path, "site_a", "hash-a", 1.5, 0.8)
    second = _write_variant(tmp_path, "site_b", "hash-b", 1.500005, 0.6)
    output = tmp_path / "grid_result.json"
    script = MODULE.with_name("score_constrained_model_grid.py")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--target-phase",
            "doped",
            "--evidence-file",
            str(evidence),
            "--variant",
            f"site_a={first}",
            "--variant",
            f"site_b={second}",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    archive = json.loads(output.read_text(encoding="utf-8"))
    assert archive["selection"]["selected_label"] == "site_b"
    assert archive["status"] == "review"
    assert archive["evidence"]["sha256"]
    assert archive["evidence"]["validated_contract"]["claim_scope"] == "model_comparison_only"


def test_constrained_model_grid_rejects_generic_evidence_and_incomplete_summary(tmp_path):
    first = _write_variant(tmp_path, "site_a", "hash-a", 1.5, 0.8)
    second = _write_variant(tmp_path, "site_b", "hash-b", 1.6, 0.6)
    script = MODULE.with_name("score_constrained_model_grid.py")
    generic_evidence = tmp_path / "generic.json"
    generic_evidence.write_text('{"method":"ICP"}', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--target-phase",
            "doped",
            "--evidence-file",
            str(generic_evidence),
            "--variant",
            f"site_a={first}",
            "--variant",
            f"site_b={second}",
            "--output",
            str(tmp_path / "rejected-generic.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "schema_version" in result.stderr

    evidence = tmp_path / "valid-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "constrained_model_grid",
                "status": "pass",
                "basis": "predeclared hypotheses",
                "target_phase": "doped",
                "variant_labels": ["site_a", "site_b"],
                "claim_scope": "model_comparison_only",
            }
        ),
        encoding="utf-8",
    )
    incomplete = json.loads(first.read_text(encoding="utf-8"))
    incomplete.pop("scientific_assessment")
    first.write_text(json.dumps(incomplete), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--target-phase",
            "doped",
            "--evidence-file",
            str(evidence),
            "--variant",
            f"site_a={first}",
            "--variant",
            f"site_b={second}",
            "--output",
            str(tmp_path / "rejected-summary.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "scientific_assessment" in result.stderr
