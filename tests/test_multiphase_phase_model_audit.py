import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "gsas-ii-multiphase-refinement" / "scripts"

CORE_SPEC = importlib.util.spec_from_file_location("qpa_core", SCRIPTS / "qpa_core.py")
qpa_core = importlib.util.module_from_spec(CORE_SPEC)
assert CORE_SPEC.loader is not None
CORE_SPEC.loader.exec_module(qpa_core)
sys.modules["qpa_core"] = qpa_core

COMMON_SPEC = importlib.util.spec_from_file_location(
    "qpa_common_problems", SCRIPTS / "qpa_common_problems.py"
)
qpa_common_problems = importlib.util.module_from_spec(COMMON_SPEC)
assert COMMON_SPEC.loader is not None
COMMON_SPEC.loader.exec_module(qpa_common_problems)
sys.modules["qpa_common_problems"] = qpa_common_problems

AUDIT_SPEC = importlib.util.spec_from_file_location(
    "audit_phase_models", SCRIPTS / "audit_phase_models.py"
)
audit_phase_models = importlib.util.module_from_spec(AUDIT_SPEC)
assert AUDIT_SPEC.loader is not None
AUDIT_SPEC.loader.exec_module(audit_phase_models)
sys.modules["audit_phase_models"] = audit_phase_models

DRIVER_SPEC = importlib.util.spec_from_file_location(
    "run_multiphase_qpa", SCRIPTS / "run_multiphase_qpa.py"
)
run_multiphase_qpa = importlib.util.module_from_spec(DRIVER_SPEC)
assert DRIVER_SPEC.loader is not None
DRIVER_SPEC.loader.exec_module(run_multiphase_qpa)


def test_source_composition_generates_missing_multiplicities_from_symmetry():
    block = {
        "_chemical_formula_sum": "Al2 O3",
        "_symmetry_space_group_name_H-M": "R -3 c",
        "_atom_site_type_symbol": ["Al", "O"],
        "_atom_site_fract_x": [0.0, 0.3064],
        "_atom_site_fract_y": [0.0, 0.0],
        "_atom_site_fract_z": [0.35228, 0.25],
        "_atom_site_occupancy": [1.0, 1.0],
    }

    def multiplicity(_space_group, coordinates):
        return 12 if coordinates[0] == 0.0 else 18

    composition, issues, metadata = audit_phase_models.source_cell_composition(
        block, site_multiplicity=multiplicity
    )
    assert composition == {"Al": 12.0, "O": 18.0}
    assert issues == []
    assert metadata["multiplicity_source"] == (
        "coordinate_orbit_from_declared_space_group"
    )
    assert metadata["formula_inferred_z_comparison"]["status"] == "pass"
    assert metadata["formula_inferred_z_comparison"]["inferred_z"] == 6.0


def test_source_formula_z_fallback_remains_available():
    block = {
        "_chemical_formula_sum": "Ca F2",
        "_cell_formula_units_Z": 4,
    }
    composition, issues, metadata = audit_phase_models.source_cell_composition(block)
    assert composition == {"Ca": 4.0, "F": 8.0}
    assert issues == []
    assert metadata["formula_z_comparison"]["status"] == "pass"


def test_international_tables_number_can_drive_missing_multiplicity():
    block = {
        "_chemical_formula_sum": "Ca F2",
        "_space_group_IT_number": 225,
        "_atom_site_type_symbol": ["Ca", "F"],
        "_atom_site_fract_x": [0.0, 0.25],
        "_atom_site_fract_y": [0.0, 0.25],
        "_atom_site_fract_z": [0.0, 0.25],
    }
    seen = []

    def multiplicity(space_group, coordinates):
        seen.append((space_group, coordinates))
        return 4 if coordinates[0] == 0.0 else 8

    composition, issues, metadata = audit_phase_models.source_cell_composition(
        block, site_multiplicity=multiplicity
    )
    assert composition == {"Ca": 4.0, "F": 8.0}
    assert issues == []
    assert seen[0][0] == "number:225"
    assert metadata["formula_inferred_z_comparison"]["status"] == "pass"


def test_phase_roles_default_to_sample_and_exclude_hardware_role():
    roles = run_multiphase_qpa.parse_phase_roles(
        ["window=hardware", "standard=internal_standard"],
        ["active_a", "active_b", "window", "standard"],
    )
    assert roles == {
        "active_a": "sample",
        "active_b": "sample",
        "window": "hardware",
        "standard": "internal_standard",
    }


def test_common_problem_cli_parsers_bind_names_to_declared_phases():
    assert run_multiphase_qpa.parse_named_axes(
        ["active_a=0,0,1"], ["active_a", "active_b"]
    ) == {"active_a": (0, 0, 1)}
    assert run_multiphase_qpa.parse_named_intervals(
        ["active_a=0.9,1.1", "active_b=1,1"],
        ["active_a", "active_b"],
        "microabsorption multiplier",
    ) == {"active_a": (0.9, 1.1), "active_b": (1.0, 1.0)}


def test_explicit_symmetry_operations_can_supply_site_multiplicity():
    block = {
        "_chemical_formula_sum": "Na Cl",
        "_space_group_symop_operation_xyz": [
            "x,y,z",
            "1/2+x,1/2+y,1/2+z",
        ],
        "_atom_site_type_symbol": ["Na", "Cl"],
        "_atom_site_fract_x": [0.0, 0.25],
        "_atom_site_fract_y": [0.0, 0.25],
        "_atom_site_fract_z": [0.0, 0.25],
    }
    composition, issues, metadata = audit_phase_models.source_cell_composition(block)
    assert composition == {"Na": 2.0, "Cl": 2.0}
    assert issues == []
    assert metadata["multiplicity_source"] == (
        "coordinate_orbit_from_explicit_symmetry_operations"
    )


def test_invalid_declared_space_group_falls_back_to_explicit_operations():
    block = {
        "_chemical_formula_sum": "Na Cl",
        "_symmetry_space_group_name_H-M": "unparseable-setting",
        "_symmetry_equiv_pos_as_xyz": ["x,y,z", "1/2+x,1/2+y,1/2+z"],
        "_atom_site_type_symbol": ["Na", "Cl"],
        "_atom_site_fract_x": [0.0, 0.25],
        "_atom_site_fract_y": [0.0, 0.25],
        "_atom_site_fract_z": [0.0, 0.25],
    }

    def invalid_group(_space_group, _coordinates):
        raise ValueError("space group cannot be parsed")

    composition, _, metadata = audit_phase_models.source_cell_composition(
        block, site_multiplicity=invalid_group
    )
    assert composition == {"Na": 2.0, "Cl": 2.0}
    assert metadata["multiplicity_source"] == (
        "coordinate_orbit_from_explicit_symmetry_operations"
    )
    assert "declared_space_group_orbit_error" in metadata


def test_sample_normalized_uncertainty_is_part_of_candidate_gate():
    candidate = {
        "phase_roles": {"A": "sample", "B": "sample", "window": "hardware"},
        "sample_normalized_mass_fractions": {
            "A": {"value": 0.6, "esd": 0.01},
            "B": {"value": 0.4, "esd": float("nan")},
        },
    }
    result = run_multiphase_qpa.assess_sample_normalization(
        candidate, {"status": "pass", "hard_failures": [], "review_flags": []}
    )
    assert result["status"] == "review"
    assert "missing_or_nonpositive_sample_esd:B" in result["review_flags"]

    candidate["sample_normalized_mass_fractions"]["B"] = {
        "value": 0.3,
        "esd": 0.01,
    }
    failed = run_multiphase_qpa.assess_sample_normalization(
        candidate, {"status": "pass", "hard_failures": [], "review_flags": []}
    )
    assert failed["status"] == "fail"
    assert "invalid_sample_normalized_mass_fractions" in failed["hard_failures"]


def test_calibration_summary_must_hash_match_instrument(tmp_path):
    instrument = tmp_path / "calibrated.instprm"
    instrument.write_text("# instrument profile\n", encoding="utf-8")
    summary = tmp_path / "calibration_summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "pass",
                "selected_candidate": "03_w",
                "selected_profile_artifact": {
                    "path": str(instrument),
                    "sha256": qpa_core.sha256_file(instrument),
                },
            }
        ),
        encoding="utf-8",
    )
    record = run_multiphase_qpa.calibration_record_from_summary(summary, instrument)
    assert record["selected_profile_artifact"]["sha256"] == qpa_core.sha256_file(
        instrument
    )

    instrument.write_text("# changed profile\n", encoding="utf-8")
    try:
        run_multiphase_qpa.calibration_record_from_summary(summary, instrument)
    except ValueError as exc:
        assert "hash does not match" in str(exc)
    else:
        raise AssertionError("a changed instrument must invalidate calibration evidence")
