from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "gsas-ii-rietveld-refinement"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from run_sequential_refinement import (  # noqa: E402
    load_manifest,
    parse_atom_flags,
    parse_axes,
    parse_phase_options,
)
from sequential_audit import compare_directions  # noqa: E402


def result_frame(
    frame_id: str,
    *,
    rwp: float,
    converged: bool = True,
    shift: float = 0.2,
) -> dict:
    cell = {
        key: {"value": value}
        for key, value in {
            "a": 5.0,
            "b": 5.1,
            "c": 7.0,
            "alpha": 90.0,
            "beta": 90.0,
            "gamma": 90.0,
            "volume": 178.5,
        }.items()
    }
    return {
        "frame_id": frame_id,
        "order": 0,
        "metrics": {"Rwp": {"value": rwp}},
        "convergence": {
            "converged": converged,
            "SVD0": 0,
            "max_shift_over_su": shift,
            "frozen_variables": [],
        },
        "correlations": {"max_abs_percent": 80.0},
        "cells": {"phase": cell},
        "mass_fractions": {"phase": {"value": 1.0}},
    }


class ManifestTests(unittest.TestCase):
    def test_manifest_preserves_order_and_numeric_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patterns = []
            for name in ("b.xy", "a.xy"):
                path = root / name
                path.write_text("1 2\n2 3\n", encoding="utf-8")
                patterns.append(path)
            manifest = root / "manifest.csv"
            with manifest.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(
                    ["frame_id", "order", "pattern_path", "temperature_K"]
                )
                writer.writerow(["second", 1, patterns[0].name, 300])
                writer.writerow(["first", 0, patterns[1].name, 290])
            frames = load_manifest(
                manifest, ["phase"], allow_missing_metadata=False
            )
        self.assertEqual(["first", "second"], [row["frame_id"] for row in frames])
        self.assertEqual(290.0, frames[0]["metadata"]["temperature_K"])

    def test_phase_option_and_axis_validation(self) -> None:
        self.assertEqual(
            ["off", "isotropic"],
            parse_phase_options(
                ["off", "isotropic"],
                2,
                option="--size-model",
                allowed={"off", "isotropic", "uniaxial"},
                default="off",
            ),
        )
        self.assertEqual([(0, 1, 0)], parse_axes(["0,1,0"], 1))
        self.assertEqual(["XU", ""], parse_atom_flags(["UX", "none"], 2))


class AuditClassificationTests(unittest.TestCase):
    def test_path_dependence_is_review_not_hard_failure(self) -> None:
        forward = {
            "frames": [result_frame("f0", rwp=10.0, shift=2.0)],
            "missing_frames": [],
            "gpx": {"path": "forward.gpx", "sha256": "a"},
        }
        reverse = {
            "frames": [result_frame("f0", rwp=11.0)],
            "missing_frames": [],
            "gpx": {"path": "reverse.gpx", "sha256": "b"},
        }
        audit = compare_directions(forward, reverse)
        self.assertEqual("review", audit["status"])
        self.assertEqual([], audit["hard_failures"])
        self.assertTrue(audit["review_flags"])

    def test_nonconvergence_is_hard_failure(self) -> None:
        forward = {
            "frames": [
                result_frame("f0", rwp=10.0, converged=False)
            ],
            "missing_frames": [],
            "gpx": {"path": "forward.gpx", "sha256": "a"},
        }
        reverse = {
            "frames": [result_frame("f0", rwp=10.0)],
            "missing_frames": [],
            "gpx": {"path": "reverse.gpx", "sha256": "b"},
        }
        audit = compare_directions(forward, reverse)
        self.assertEqual("fail", audit["status"])
        self.assertTrue(audit["hard_failures"])


if __name__ == "__main__":
    unittest.main()
