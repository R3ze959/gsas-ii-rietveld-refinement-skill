from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


MODULE = (
    Path(__file__).resolve().parents[1]
    / "rietveld-plotting"
    / "scripts"
    / "make_rietveld_plot.py"
)
SPEC = importlib.util.spec_from_file_location("make_rietveld_plot", MODULE)
plotting = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(plotting)


def profile() -> dict[str, np.ndarray]:
    x = np.linspace(10.0, 20.0, 101)
    background = np.full_like(x, 10.0)
    calculated = background + 100.0 * np.exp(-((x - 15.0) / 0.25) ** 2)
    observed = calculated + np.sin(x)
    return {
        "TwoTheta": x,
        "Observed": observed,
        "CalculatedLine": calculated,
        "Background": background,
        "Difference": observed - calculated,
    }


def reflections() -> list[dict[str, object]]:
    return [
        {"phase": "major", "two_theta": 15.0, "intensity": 100.0, "hkl": (1, 0, 0), "label": "(100)"},
        {"phase": "major", "two_theta": 18.0, "intensity": 40.0, "hkl": (1, 1, 0), "label": "(110)"},
        {"phase": "minor", "two_theta": 15.0, "intensity": 20.0, "hkl": (0, 0, 1), "label": "(001)"},
        {"phase": "minor", "two_theta": 16.5, "intensity": 15.0, "hkl": (1, 0, 1), "label": "(101)"},
    ]


def test_auto_layout_separates_multiphase_bragg_rows_without_deduplicating_phases():
    prepared = plotting.prepare_plot_data(
        profile(), reflections(), 10.0, 20.0, 1, True, "auto"
    )
    assert prepared["BraggLayout"] == "separate"
    assert [row["phase"] for row in prepared["BraggRows"]] == ["major", "minor"]
    assert prepared["BraggRows"][0]["positions"].tolist() == [15.0, 18.0]
    assert prepared["BraggRows"][1]["positions"].tolist() == [15.0, 16.5]
    assert float(prepared["BraggRows"][1]["y1"][0]) < float(
        prepared["BraggRows"][0]["y0"][0]
    )
    assert float(np.nanmedian(prepared["DifferenceOffset"])) < float(
        prepared["BraggRows"][-1]["y0"][0]
    )


def test_single_phase_auto_layout_preserves_one_combined_row():
    single = [row for row in reflections() if row["phase"] == "major"]
    prepared = plotting.prepare_plot_data(
        profile(), single, 10.0, 20.0, 1, True, "auto"
    )
    assert prepared["BraggLayout"] == "combined"
    assert len(prepared["BraggRows"]) == 1
    assert prepared["BraggRows"][0]["phase"] == "major"


def test_multiphase_render_adds_phase_names_but_keeps_locked_profile_elements():
    prepared = plotting.prepare_plot_data(
        profile(), reflections(), 10.0, 20.0, 1, True, "auto"
    )
    figure = plotting.plot_rietveld(
        prepared,
        reflections()[:2],
        {"Rwp": 4.0, "Rp": 3.0, "GOF": 1.2},
        10.0,
        20.0,
        None,
        False,
        False,
        True,
        4.05,
        3.35,
    )
    try:
        axes = figure.axes[0]
        text = {item.get_text() for item in axes.texts}
        assert {"major", "minor"}.issubset(text)
        assert len(axes.collections) == 2
        assert axes.lines[0].get_marker() == "o"
        assert axes.lines[1].get_color() == "black"
        assert axes.lines[2].get_color() == plotting.DIFFERENCE_COLOR
    finally:
        plt.close(figure)
