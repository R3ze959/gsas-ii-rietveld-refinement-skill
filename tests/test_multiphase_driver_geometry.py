from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "gsas-ii-multiphase-refinement"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "run_multiphase_qpa", SCRIPTS / "run_multiphase_qpa.py"
)
driver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(driver)


class FakeHistogram:
    def __init__(self, sample_parameters):
        self.data = {"Sample Parameters": sample_parameters}
        self.refinements = []

    def set_refinements(self, payload):
        self.refinements.append(payload)


def test_flat_plate_prefers_shift_for_sample_position():
    histogram = FakeHistogram(
        {"Scale": [1.0, True], "Shift": [0.0, False], "DisplaceX": [0.0, False]}
    )
    assert driver.sample_position_parameter(histogram) == "Shift"
    assert driver.refine_sample_position(histogram) == "Shift"
    assert histogram.refinements[-1] == {"Sample Parameters": ["Shift"]}


def test_debye_scherrer_uses_displacement_when_shift_is_absent():
    histogram = FakeHistogram(
        {"Scale": [1.0, True], "DisplaceX": [0.25, False], "DisplaceY": [0.0, False]}
    )
    assert driver.sample_position_parameter(histogram) == "DisplaceX"
    assert driver.refine_sample_position(histogram) == "DisplaceX"
    assert driver.sample_position_value(histogram) == 0.25
    assert histogram.refinements[-1] == {"Sample Parameters": ["DisplaceX"]}


def test_positionless_geometry_skips_position_refinement():
    histogram = FakeHistogram({"Scale": [1.0, True]})
    assert driver.sample_position_parameter(histogram) is None
    assert driver.refine_sample_position(histogram) is None
    assert histogram.refinements == []
