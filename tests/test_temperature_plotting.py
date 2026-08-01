from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPOSITORY
    / "rietveld-plotting"
    / "scripts"
    / "make_temperature_series_plot.py"
)
sys.path.insert(0, str(SCRIPT.parent))

from make_temperature_series_plot import read_pattern, validate_audit  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_pattern(root: Path, index: int) -> Path:
    path = root / f"frame_{index:03d}.xy"
    path.write_text(
        "\n".join(
            f"{10 + point * 0.1:.3f} "
            f"{50 + index * 2 + 700 / (1 + ((point - 40 - index) / 3) ** 2):.6f}"
            for point in range(101)
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def create_fixture(root: Path, audit_status: str = "pass") -> tuple[Path, Path]:
    fake_gpx = root / "sequential_forward.gpx"
    fake_gpx.write_bytes(b"read-only-gpx-fixture")
    frames = []
    for index, temperature in enumerate((100.0, 200.0, 300.0)):
        pattern = create_pattern(root, index)
        frames.append(
            {
                "frame_id": f"f{index:03d}",
                "order": index,
                "histogram": f"PWDR f{index:03d}",
                "pattern": {
                    "path": str(pattern),
                    "bytes": pattern.stat().st_size,
                    "sha256": sha256(pattern),
                },
                "metadata": {
                    "temperature_K": temperature,
                    "source_frame": str(index + 1),
                },
                "cells": {
                    "PhaseA": {
                        "a": {
                            "value": 4.0 + index * 0.01,
                            "esd": 0.001,
                            "symmetry_independent": True,
                        },
                        "b": {
                            "value": 4.0,
                            "esd": None,
                            "symmetry_independent": False,
                        },
                        "c": {
                            "value": 5.0 + index * 0.02,
                            "esd": 0.002,
                            "symmetry_independent": True,
                        },
                        "alpha": {
                            "value": 90.0,
                            "esd": None,
                            "symmetry_independent": False,
                        },
                        "beta": {
                            "value": 90.0,
                            "esd": None,
                            "symmetry_independent": False,
                        },
                        "gamma": {
                            "value": 90.0,
                            "esd": None,
                            "symmetry_independent": False,
                        },
                        "volume": {
                            "value": 80.0 + index * 0.4,
                            "esd": 0.02,
                            "symmetry_independent": True,
                        },
                    }
                },
            }
        )
    results = {
        "schema_version": 1,
        "direction": "forward",
        "gpx": {
            "path": str(fake_gpx),
            "bytes": fake_gpx.stat().st_size,
            "sha256": sha256(fake_gpx),
        },
        "frames": frames,
    }
    results_path = root / "sequential_results_forward.json"
    results_path.write_text(json.dumps(results), encoding="utf-8")
    audit_path = root / "sequential_audit.json"
    audit_path.write_text(json.dumps({"status": audit_status}), encoding="utf-8")
    return results_path, audit_path


class TemperaturePlottingTests(unittest.TestCase):
    def test_fxye_centidegrees_are_converted_without_intensity_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.fxye"
            path.write_text(
                "BANK 1 3 3 CONS 1000.0 10.0 0 0 FXYE\n"
                "1000.0 10.0 1.0\n1010.0 20.0 1.0\n1020.0 30.0 1.0\n",
                encoding="utf-8",
            )
            x, y, parser = read_pattern(path)
        self.assertEqual([10.0, 10.1, 10.2], x.tolist())
        self.assertEqual([10.0, 20.0, 30.0], y.tolist())
        self.assertTrue(parser["fxye_centidegree_conversion"])

    def test_failed_audit_needs_explicit_diagnostic_override(self) -> None:
        with self.assertRaises(SystemExit):
            validate_audit({"status": "fail"}, allow_failed=False)
        self.assertEqual("fail", validate_audit({"status": "fail"}, allow_failed=True))

    def test_end_to_end_writes_three_figures_and_integrity_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results_path, audit_path = create_fixture(root)
            output = root / "plots"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--results",
                    str(results_path),
                    "--audit",
                    str(audit_path),
                    "--out-dir",
                    str(output),
                    "--sample-id",
                    "fixture",
                    "--formats",
                    "png",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
            output_paths = [
                Path(group["png"])
                for group in payload["outputs"].values()
            ]
            self.assertTrue(all(path.is_file() for path in output_paths))
            self.assertEqual("temperature-series-origin-v1", manifest["style_profile"])
            self.assertEqual("pass", manifest["audit_status"])
            self.assertEqual(3, manifest["frame_count"])
            self.assertEqual("all source hashes identical before and after plotting", manifest["integrity"])
            self.assertFalse(manifest["intensity_display"]["smoothed"])

    def test_operando_frame_coordinate_is_not_relabeled_as_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results_path, audit_path = create_fixture(root, audit_status="review")
            output = root / "plots"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--results",
                    str(results_path),
                    "--audit",
                    str(audit_path),
                    "--out-dir",
                    str(output),
                    "--sample-id",
                    "operando-fixture",
                    "--series-key",
                    "source_frame",
                    "--series-label",
                    "Frame",
                    "--formats",
                    "png",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual("operando-series-origin-v1", manifest["style_profile"])
        self.assertEqual("review", manifest["audit_status"])
        self.assertEqual("source_frame", manifest["series_coordinate"]["metadata_key"])
        self.assertEqual("Frame", manifest["series_coordinate"]["label"])
        self.assertNotIn("temperature", manifest)


if __name__ == "__main__":
    unittest.main()
