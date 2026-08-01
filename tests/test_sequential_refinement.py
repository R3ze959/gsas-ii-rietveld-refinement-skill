from __future__ import annotations

import csv
import json
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
    audit_manifest_metadata,
    build_anchor_segments,
    hstrain_offsets_for_cells,
    inspect_text_pattern,
    load_manifest,
    parse_atom_flags,
    parse_axes,
    parse_phase_options,
    read_reference_cells,
)
from sequential_audit import (  # noqa: E402
    _last_cycle_shift_over_su,
    compare_directions,
)
from build_sequential_manifest import (  # noqa: E402
    build_manifest,
    merge_exact,
    merge_nearest_time,
)
from select_sequential_candidate import (  # noqa: E402
    candidate_rank,
    select_candidates,
    verified_json,
)
from refinement_audit import sha256  # noqa: E402


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
            "max_last_cycle_shift_over_su": shift,
            "max_total_shift_over_su": shift * 10,
            "frozen_variables": [],
        },
        "correlations": {"max_abs_percent": 80.0},
        "cells": {"phase": cell},
        "mass_fractions": {"phase": {"value": 1.0, "esd": 0.01}},
        "residual_audit": {"positive_local_maxima": []},
    }


class ManifestTests(unittest.TestCase):
    def test_sequential_candidate_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "payload.json"
            payload.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                verified_json(
                    {"path": str(payload), "sha256": "0" * 64},
                    label="payload",
                )

    def test_sequential_candidate_prefers_pass_before_rwp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            summaries = []
            for name, status, rwp in (
                ("lower_rwp_review", "review", 8.0),
                ("stable_pass", "pass", 9.0),
            ):
                run = root / name
                run.mkdir()
                manifest = run / "manifest.json"
                audit = run / "audit.json"
                forward = run / "forward.json"
                reverse = run / "reverse.json"
                summary = run / "summary.json"
                manifest.write_text(
                    json.dumps(
                        {
                            "settings": {"model": name},
                            "frames": [
                                {
                                    "frame_id": "f0",
                                    "order": 0,
                                    "pattern": {"sha256": "same-pattern"},
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                audit.write_text(
                    json.dumps(
                        {
                            "status": status,
                            "hard_failures": [],
                            "review_flags": (
                                [{"reason": "review"}]
                                if status == "review"
                                else []
                            ),
                            "frame_comparisons": [
                                {"Rwp_absolute_delta": 0.05}
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                result = {
                    "frames": [
                        {
                            "frame_id": "f0",
                            "metrics": {"Rwp": {"value": rwp}},
                        }
                    ]
                }
                forward.write_text(json.dumps(result), encoding="utf-8")
                reverse.write_text(json.dumps(result), encoding="utf-8")
                validation = run / "validation.json"
                validation.write_text(
                    json.dumps({"status": "pass"}),
                    encoding="utf-8",
                )
                summary.write_text(
                    json.dumps(
                        {
                            "sample_id": "sample",
                            "manifest": {
                                "path": str(manifest),
                                "sha256": sha256(manifest),
                            },
                            "audit_outputs": {
                                "audit": {
                                    "path": str(audit),
                                    "sha256": sha256(audit),
                                },
                                "forward_json": {
                                    "path": str(forward),
                                    "sha256": sha256(forward),
                                },
                                "reverse_json": {
                                    "path": str(reverse),
                                    "sha256": sha256(reverse),
                                },
                                "report_validation": {
                                    "path": str(validation),
                                    "sha256": sha256(validation),
                                },
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                summaries.append(summary)
            selection = select_candidates(summaries)
        self.assertEqual("selected", selection["status"])
        selected_hash = selection["selected_summary_sha256"]
        selected = next(
            item
            for item in selection["candidates"]
            if item["summary"]["sha256"] == selected_hash
        )
        self.assertEqual("pass", selected["status"])

    def test_direction_deltas_within_tolerance_are_ranked_by_rwp(self) -> None:
        def candidate(delta: float, rwp: float, digest: str) -> dict:
            return {
                "status": "review",
                "hard_failure_count": 0,
                "review_flag_count": 1,
                "median_Rwp_direction_delta": delta,
                "median_Rwp": rwp,
                "settings": {
                    "audit_tolerances": {"rwp_tolerance": 0.25}
                },
                "summary": {"sha256": digest},
            }

        tiny_delta_high_rwp = candidate(0.000002, 9.47, "a" * 64)
        small_delta_low_rwp = candidate(0.000143, 8.41, "b" * 64)
        self.assertLess(
            candidate_rank(small_delta_low_rwp),
            candidate_rank(tiny_delta_high_rwp),
        )

    def test_direction_delta_above_tolerance_still_ranks_first(self) -> None:
        def candidate(delta: float, rwp: float, digest: str) -> dict:
            return {
                "status": "review",
                "hard_failure_count": 0,
                "review_flag_count": 1,
                "median_Rwp_direction_delta": delta,
                "median_Rwp": rwp,
                "settings": {
                    "audit_tolerances": {"rwp_tolerance": 0.25}
                },
                "summary": {"sha256": digest},
            }

        stable = candidate(0.20, 10.0, "a" * 64)
        path_dependent = candidate(0.30, 7.0, "b" * 64)
        self.assertLess(candidate_rank(stable), candidate_rank(path_dependent))

    def test_reference_cells_are_copied_from_one_anchor(self) -> None:
        class FakePhase:
            name = "phase"
            data = {
                "General": {
                    "Cell": [True, 5.0, 5.1, 7.0, 90, 90, 90, 178.5]
                }
            }

        class FakeProject:
            @staticmethod
            def phases() -> list:
                return [FakePhase()]

        class FakeG2:
            @staticmethod
            def G2Project(_path: str) -> FakeProject:
                return FakeProject()

        cells = read_reference_cells(
            FakeG2,
            Path("anchor.gpx"),
            ["phase"],
        )
        self.assertEqual(5.0, cells["phase"][1])
        cells["phase"][1] = 9.0
        self.assertEqual(5.0, FakePhase.data["General"]["Cell"][1])

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

    def test_pattern_preflight_and_metadata_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pattern = Path(temporary) / "pattern.xy"
            pattern.write_text(
                "\n".join(f"{10 + index * 0.02:.4f} {100 + index}" for index in range(30))
                + "\n",
                encoding="utf-8",
            )
            inspection = inspect_text_pattern(pattern)
        self.assertEqual(30, inspection["point_count"])
        self.assertAlmostEqual(0.02, inspection["median_step"])
        frames = [
            {"metadata": {"time_s": 0.0}},
            {"metadata": {"time_s": 10.0}},
        ]
        audit = audit_manifest_metadata(frames, file_order_only=False)
        self.assertEqual("time_synchronized", audit["mode"])

    def test_anchor_segments_cover_each_frame_once(self) -> None:
        frames = [
            {"frame_id": f"f{index}", "order": index}
            for index in range(10)
        ]
        accepted = {"f0", "f5", "f9"}
        forward = build_anchor_segments(
            frames, accepted, direction="forward"
        )
        reverse = build_anchor_segments(
            frames, accepted, direction="reverse"
        )
        self.assertEqual(
            [f"f{index}" for index in range(10)],
            [frame["frame_id"] for segment in forward for frame in segment["frames"]],
        )
        self.assertEqual(
            [f"f{index}" for index in reversed(range(10))],
            [frame["frame_id"] for segment in reverse for frame in segment["frames"]],
        )
        self.assertEqual(["f0", "f5"], [item["checkpoint_frame_id"] for item in forward])
        self.assertEqual(["f9", "f5"], [item["checkpoint_frame_id"] for item in reverse])

    def test_anchor_cell_is_converted_to_hstrain_offsets(self) -> None:
        offsets = hstrain_offsets_for_cells(
            reference_cell=[False, 1, 2, 3, 90, 90, 90, 6],
            target_cell=[False, 1.1, 2.2, 3.3, 90, 90, 90, 8],
            strain_names=["D11", "D33"],
            cell_to_reciprocal=lambda cell: [
                cell[0],
                cell[1],
                cell[2],
                cell[3],
                cell[4],
                cell[5],
            ],
        )
        self.assertEqual([0.1, 0.3], [round(value, 6) for value in offsets])

    def test_exact_and_nearest_time_metadata_sync(self) -> None:
        frames = [
            {"frame_id": "f0", "order": "0", "pattern_path": "a.xy"},
            {"frame_id": "f1", "order": "1", "pattern_path": "b.xy"},
        ]
        exact, exact_audit = merge_exact(
            frames,
            [
                {"frame_id": "f0", "voltage_V": "3.0"},
                {"frame_id": "f1", "voltage_V": "2.9"},
            ],
            join_on="frame_id",
        )
        self.assertEqual("2.9", exact[1]["voltage_V"])
        self.assertEqual(2, exact_audit["matched_count"])
        timed_frames = [
            {**frames[0], "xrd_time_s": "10"},
            {**frames[1], "xrd_time_s": "20"},
        ]
        nearest, nearest_audit = merge_nearest_time(
            timed_frames,
            [
                {"ec_time_s": "9.8", "voltage_V": "3.0"},
                {"ec_time_s": "20.3", "voltage_V": "2.9"},
            ],
            frame_time_column="xrd_time_s",
            metadata_time_column="ec_time_s",
            maximum_delta_s=0.5,
        )
        self.assertEqual(2, nearest_audit["matched_count"])
        self.assertAlmostEqual(-0.2, float(nearest[0]["sync_delta_s"]))
        self.assertEqual([0, 1], [item["metadata_row_index"] for item in nearest_audit["matches"]])

    def test_nearest_time_metadata_rows_cannot_be_reused(self) -> None:
        frames = [
            {
                "frame_id": "f0",
                "order": "0",
                "pattern_path": "a.xy",
                "xrd_time_s": "10.0",
            },
            {
                "frame_id": "f1",
                "order": "1",
                "pattern_path": "b.xy",
                "xrd_time_s": "10.1",
            },
        ]
        nearest, audit = merge_nearest_time(
            frames,
            [
                {"ec_time_s": "10.0", "voltage_V": "3.0"},
                {"ec_time_s": "30.0", "voltage_V": "2.9"},
            ],
            frame_time_column="xrd_time_s",
            metadata_time_column="ec_time_s",
            maximum_delta_s=0.5,
        )
        self.assertEqual(1, audit["matched_count"])
        self.assertEqual(["f1"], audit["unmatched_frame_ids"])
        self.assertEqual([0], [item["metadata_row_index"] for item in audit["matches"]])
        self.assertEqual(["f0"], [item["frame_id"] for item in nearest])

    def test_manifest_builder_binds_output_hash_and_match_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frame_index = root / "frames.csv"
            metadata = root / "metadata.csv"
            output = root / "manifest.csv"
            patterns = []
            for index in range(2):
                pattern = root / f"f{index}.xy"
                pattern.write_text("10 1\n11 2\n", encoding="utf-8")
                patterns.append(pattern)
            with frame_index.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["frame_id", "order", "pattern_path", "xrd_time_s"],
                )
                writer.writeheader()
                for index, pattern in enumerate(patterns):
                    writer.writerow(
                        {
                            "frame_id": f"f{index}",
                            "order": index,
                            "pattern_path": pattern,
                            "xrd_time_s": 10 + index * 10,
                        }
                    )
            with metadata.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["ec_time_s", "voltage_V"],
                )
                writer.writeheader()
                writer.writerow({"ec_time_s": 10.1, "voltage_V": 3.0})
                writer.writerow({"ec_time_s": 20.2, "voltage_V": 2.9})
            audit = build_manifest(
                frame_index=frame_index,
                metadata_csv=metadata,
                output=output,
                join_on="nearest-time",
                frame_time_column="xrd_time_s",
                metadata_time_column="ec_time_s",
                maximum_delta_s=0.5,
            )
            self.assertEqual(sha256(output), audit["output_manifest_sha256"])
            self.assertEqual(str(output.resolve()), audit["output_manifest"])
            self.assertEqual([0, 1], [item["metadata_row_index"] for item in audit["matches"]])
            self.assertTrue(output.with_suffix(".csv.sync.json").is_file())


class AuditClassificationTests(unittest.TestCase):
    def test_missing_mass_fraction_esd_is_reviewed(self) -> None:
        forward_frame = result_frame("f0", rwp=10.0)
        reverse_frame = result_frame("f0", rwp=10.0)
        forward_frame["mass_fractions"]["phase"]["esd"] = None
        reverse_frame["mass_fractions"]["phase"]["esd"] = None
        audit = compare_directions(
            {
                "frames": [forward_frame],
                "missing_frames": [],
                "gpx": {"path": "forward.gpx", "sha256": "a"},
            },
            {
                "frames": [reverse_frame],
                "missing_frames": [],
                "gpx": {"path": "reverse.gpx", "sha256": "b"},
            },
        )
        self.assertEqual("review", audit["status"])
        self.assertEqual(2, len(audit["uncertainty_gaps"]))

    def test_final_cycle_shift_is_distinct_from_total_run_shift(self) -> None:
        value = _last_cycle_shift_over_su(
            {
                "varyList": ["a", "b"],
                "sig": [2.0, 4.0],
                "Rvals": {
                    "Max shft/sig": 60.0,
                    "lastShifts": {"a": 0.5, "b": -2.0},
                },
            }
        )
        self.assertEqual(0.5, value)

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

    def test_persistent_major_residual_is_hard_failure(self) -> None:
        first = result_frame("f0", rwp=10.0)
        second = result_frame("f0", rwp=10.0)
        for frame, angle in ((first, 25.0), (second, 25.04)):
            frame["residual_audit"] = {
                "positive_local_maxima": [
                    {
                        "two_theta": angle,
                        "fraction_of_pattern_max_percent": 12.0,
                        "robust_sigma_above_residual": 9.0,
                    }
                ]
            }
        audit = compare_directions(
            {
                "frames": [first],
                "missing_frames": [],
                "gpx": {"path": "forward.gpx", "sha256": "a"},
            },
            {
                "frames": [second],
                "missing_frames": [],
                "gpx": {"path": "reverse.gpx", "sha256": "b"},
            },
        )
        self.assertEqual("fail", audit["status"])
        self.assertIn(
            "persistent positive residual",
            audit["hard_failures"][0]["reason"],
        )


if __name__ == "__main__":
    unittest.main()
