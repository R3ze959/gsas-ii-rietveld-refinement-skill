from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "rietveld-plotting" / "scripts" / "make_operando_xrd_plot.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_fixture(root: Path) -> tuple[Path, Path, Path, list[Path]]:
    frames = []
    patterns: list[Path] = []
    manifest_path = root / "manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "frame_id",
                "order",
                "pattern_path",
                "time_s",
                "current_mA",
                "voltage_V",
                "sync_delta_s",
            ],
        )
        writer.writeheader()
        for index in range(3):
            frame_id = f"f{index:03d}"
            source = root / f"{frame_id}.raw"
            source.write_bytes(f"raw-frame-{index}".encode())
            pattern = root / f"{frame_id}.xye"
            pattern.write_text(
                "\n".join(
                    f"{4 + point * 0.02:.4f} "
                    f"{30 + 500 / (1 + ((point - 40 - index * 2) / 3) ** 2):.6f} 1"
                    for point in range(101)
                )
                + "\n",
                encoding="utf-8",
            )
            patterns.append(pattern)
            frames.append(
                {
                    "frame_id": frame_id,
                    "order": index,
                    "source": {
                        "path": str(source),
                        "bytes": source.stat().st_size,
                        "sha256": sha256(source),
                    },
                    "output": {
                        "path": str(pattern),
                        "bytes": pattern.stat().st_size,
                        "sha256": sha256(pattern),
                    },
                }
            )
            writer.writerow(
                {
                    "frame_id": frame_id,
                    "order": index,
                    "pattern_path": str(pattern),
                    "time_s": 300 + index * 600,
                    "current_mA": (0.1, 0.0, -0.1)[index],
                    "voltage_V": (3.0, 4.0, 3.1)[index],
                    "sync_delta_s": 1.0,
                }
            )
    conversion_path = root / "stoe_conversion_audit.json"
    conversion_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "format": "STOE WinXPOW RAW_1.06Powdat",
                "smoothing_performed": False,
                "background_subtraction_performed": False,
                "converted_count": len(frames),
                "frames": frames,
            }
        ),
        encoding="utf-8",
    )
    sync_path = root / "manifest.csv.sync.json"
    matches = [
        {
            "frame_id": frame["frame_id"],
            "frame_order": frame["order"],
            "frame_time_s": 300 + frame["order"] * 600,
            "metadata_row_index": frame["order"],
            "metadata_time_s": 301 + frame["order"] * 600,
            "sync_delta_s": 1.0,
        }
        for frame in frames
    ]
    sync_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "nearest_time",
                "matched_count": len(frames),
                "frame_count": len(frames),
                "unmatched_frame_ids": [],
                "matches": matches,
                "maximum_absolute_sync_delta_s": 1.0,
                "maximum_delta_s": 5.0,
                "output_manifest": str(manifest_path),
                "output_manifest_sha256": sha256(manifest_path),
                "interpolation_performed": False,
            }
        ),
        encoding="utf-8",
    )
    return manifest_path, conversion_path, sync_path, patterns


class OperandoXrdPlottingTests(unittest.TestCase):
    def test_end_to_end_writes_labelled_experimental_figure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, conversion, sync, _ = create_fixture(root)
            output = root / "plots"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--conversion-audit",
                    str(conversion),
                    "--sync-audit",
                    str(sync),
                    "--out-dir",
                    str(output),
                    "--sample-id",
                    "fixture",
                    "--promotional-layout",
                    "--clean-figure",
                    "--formats",
                    "png",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            plot_manifest = json.loads(
                Path(payload["manifest"]).read_text(encoding="utf-8")
            )
            self.assertTrue(Path(payload["outputs"]["png"]).is_file())
            self.assertEqual("experimental-operando-origin-v1", plot_manifest["style_profile"])
            self.assertFalse(plot_manifest["per_frame_rietveld_claimed"])
            self.assertEqual(3, plot_manifest["frame_count"])
            self.assertEqual("log", plot_manifest["intensity_display"]["mode"])
            self.assertFalse(plot_manifest["intensity_display"]["smoothing_performed"])
            self.assertTrue(plot_manifest["sources"]["verified_raw_sources"])
            self.assertTrue(plot_manifest["electrochemistry"]["promotional_layout"])
            self.assertTrue(plot_manifest["electrochemistry"]["clean_figure"])
            self.assertIn("_promo_clean.png", payload["outputs"]["png"])

    def test_modified_converted_pattern_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, conversion, sync, patterns = create_fixture(root)
            patterns[1].write_text("4.0 999 1\n4.1 999 1\n4.2 999 1\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--conversion-audit",
                    str(conversion),
                    "--sync-audit",
                    str(sync),
                    "--out-dir",
                    str(root / "plots"),
                    "--sample-id",
                    "fixture",
                    "--formats",
                    "png",
                ],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("SHA-256 mismatch", completed.stderr + completed.stdout)

    def test_sync_audit_must_bind_the_exact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, conversion, sync, _ = create_fixture(root)
            sync_payload = json.loads(sync.read_text(encoding="utf-8"))
            sync_payload["output_manifest_sha256"] = "0" * 64
            sync.write_text(json.dumps(sync_payload), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--conversion-audit",
                    str(conversion),
                    "--sync-audit",
                    str(sync),
                    "--out-dir",
                    str(root / "plots"),
                    "--sample-id",
                    "fixture",
                    "--formats",
                    "png",
                ],
                capture_output=True,
                text=True,
            )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("manifest SHA-256 does not match", completed.stderr + completed.stdout)

    def test_stacked_view_retains_every_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, conversion, sync, _ = create_fixture(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--conversion-audit",
                    str(conversion),
                    "--sync-audit",
                    str(sync),
                    "--out-dir",
                    str(root / "plots"),
                    "--sample-id",
                    "fixture",
                    "--view",
                    "stacked",
                    "--intensity-mode",
                    "per-frame",
                    "--formats",
                    "png",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            plot_manifest = json.loads(
                Path(payload["manifest"]).read_text(encoding="utf-8")
            )
        self.assertEqual("stacked", plot_manifest["view"])
        self.assertEqual(3, plot_manifest["electrochemistry"]["frame_count_drawn"])
        self.assertEqual("per-frame", plot_manifest["intensity_display"]["mode"])
        self.assertIn("_stacked.png", payload["outputs"]["png"])

    def test_representative_broken_axis_stack_records_sampling_and_voltage_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, conversion, sync, _ = create_fixture(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--manifest",
                    str(manifest),
                    "--conversion-audit",
                    str(conversion),
                    "--sync-audit",
                    str(sync),
                    "--out-dir",
                    str(root / "plots"),
                    "--sample-id",
                    "fixture",
                    "--view",
                    "stacked",
                    "--intensity-mode",
                    "per-frame",
                    "--frame-step",
                    "2",
                    "--x-windows",
                    "4,4.7;5.2,6",
                    "--voltage-major-step",
                    "0.5",
                    "--allow-profile-overlap",
                    "--peak-gamma",
                    "0.5",
                    "--promotional-layout",
                    "--formats",
                    "png",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = json.loads(completed.stdout)
            plot_manifest = json.loads(
                Path(payload["manifest"]).read_text(encoding="utf-8")
            )
        electrochemistry = plot_manifest["electrochemistry"]
        self.assertEqual(2, electrochemistry["frame_count_drawn"])
        self.assertEqual([0, 2], electrochemistry["selected_frame_indices"])
        self.assertEqual(3, electrochemistry["voltage_trajectory_frame_count"])
        self.assertEqual(0.5, electrochemistry["voltage_major_tick_step_V"])
        self.assertTrue(electrochemistry["profile_overlap_allowed"])
        self.assertFalse(electrochemistry["no_profile_overlap_by_vertical_extent"])
        self.assertEqual(0.5, electrochemistry["peak_display_gamma"])
        self.assertTrue(electrochemistry["promotional_layout"])
        self.assertEqual([[4.0, 4.7], [5.2, 6.0]], plot_manifest["x_windows"])
        self.assertIn(
            "_stacked_broken_axis_peak_emphasis_weak_peaks_promo.png",
            payload["outputs"]["png"],
        )


if __name__ == "__main__":
    unittest.main()
