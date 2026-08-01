from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY / "gsas-ii-rietveld-refinement" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from refinement_core import classify_refinement_request  # noqa: E402


def make_pattern(root: Path, name: str) -> Path:
    path = root / name
    path.write_text(
        "\n".join(
            f"{10 + index * 0.1:.3f} {100 + index}"
            for index in range(20)
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def make_manifest(
    root: Path,
    patterns: list[Path],
    *,
    temperatures: list[float] | None = None,
) -> Path:
    path = root / "manifest.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["frame_id", "order", "pattern_path", "temperature_K"])
        for index, pattern in enumerate(patterns):
            temperature = (
                temperatures[index] if temperatures is not None else 300
            )
            writer.writerow(
                [f"f{index:03d}", index, pattern.name, temperature]
            )
    return path


class RefinementRoutingTests(unittest.TestCase):
    def test_one_integrated_pattern_routes_to_single(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pattern = make_pattern(Path(temporary), "sample.xy")
            result = classify_refinement_request(patterns=[pattern])
        self.assertEqual("single_pattern_refinement", result["classification"])
        self.assertEqual("ready", result["status"])
        self.assertEqual("run_staged_refinement.py", result["driver"])
        self.assertTrue(result["gsasii_allowed"])

    def test_valid_manifest_routes_to_sequential(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patterns = [
                make_pattern(root, "f000.xy"),
                make_pattern(root, "f001.xy"),
            ]
            manifest = make_manifest(
                root,
                patterns,
                temperatures=[300, 310],
            )
            result = classify_refinement_request(manifest=manifest)
        self.assertEqual("sequential_refinement", result["classification"])
        self.assertEqual("ready", result["status"])
        self.assertEqual(
            ["temperature_K"],
            result["inputs"]["manifest"]["varying_coordinates"],
        )

    def test_multiple_patterns_without_manifest_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patterns = [
                make_pattern(root, "a.xy"),
                make_pattern(root, "b.xy"),
            ]
            result = classify_refinement_request(patterns=patterns)
        self.assertEqual("multiple_patterns_ambiguous", result["classification"])
        self.assertEqual("needs_clarification", result["status"])
        self.assertFalse(result["gsasii_allowed"])

    def test_explicit_independent_batch_is_not_sequential(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patterns = [
                make_pattern(root, "a.xy"),
                make_pattern(root, "b.xy"),
            ]
            result = classify_refinement_request(
                patterns=patterns,
                declared_mode="batch",
            )
        self.assertEqual(
            "independent_batch_refinement",
            result["classification"],
        )
        self.assertEqual("ready", result["status"])
        self.assertNotEqual(
            "run_sequential_refinement.py",
            result["driver"],
        )

    def test_detector_frame_requires_integration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            detector = make_pattern(Path(temporary), "frame.cbf")
            result = classify_refinement_request(patterns=[detector])
        self.assertEqual(
            "detector_integration_required",
            result["classification"],
        )
        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["gsasii_allowed"])

    def test_plot_request_hands_off_without_refinement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            gpx = make_pattern(Path(temporary), "accepted.gpx")
            result = classify_refinement_request(
                accepted_gpx=[gpx],
                intent="plot",
            )
        self.assertEqual("plotting_handoff", result["classification"])
        self.assertEqual("handoff", result["status"])
        self.assertEqual("rietveld-plotting", result["target_skill"])
        self.assertFalse(result["gsasii_allowed"])

    def test_manifest_without_varying_coordinate_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patterns = [
                make_pattern(root, "f000.xy"),
                make_pattern(root, "f001.xy"),
            ]
            manifest = make_manifest(root, patterns)
            result = classify_refinement_request(manifest=manifest)
        self.assertEqual(
            "invalid_sequential_manifest",
            result["classification"],
        )
        self.assertEqual("blocked", result["status"])

    def test_single_plan_records_mandatory_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pattern = make_pattern(root, "sample.xy")
            cif = make_pattern(root, "phase.cif")
            instrument = make_pattern(root, "instrument.prm")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_staged_refinement.py"),
                    "--sample-id",
                    "route-test",
                    "--xrd",
                    str(pattern),
                    "--cif",
                    str(cif),
                    "--instrument",
                    str(instrument),
                    "--instrument-profile-status",
                    "calibrated",
                    "--plan-only",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(
            "single_pattern_refinement",
            payload["request_classification"]["classification"],
        )

    def test_sequential_plan_records_mandatory_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            patterns = [
                make_pattern(root, "f000.xy"),
                make_pattern(root, "f001.xy"),
            ]
            manifest = make_manifest(
                root,
                patterns,
                temperatures=[300, 310],
            )
            cif = make_pattern(root, "phase.cif")
            instrument = make_pattern(root, "instrument.prm")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_sequential_refinement.py"),
                    "--sample-id",
                    "route-test",
                    "--manifest",
                    str(manifest),
                    "--cif",
                    str(cif),
                    "--instrument",
                    str(instrument),
                    "--instrument-profile-status",
                    "calibrated",
                    "--plan-only",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(
            "sequential_refinement",
            payload["request_classification"]["classification"],
        )


if __name__ == "__main__":
    unittest.main()
