#!/usr/bin/env python3
"""Shared request routing and input helpers for GSAS-II refinement modes."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable


CORE_MANIFEST_FIELDS = {"frame_id", "pattern_path", "order", "phase_set"}
STANDARD_METADATA_FIELDS = (
    "time_s",
    "temperature_K",
    "voltage_V",
    "current_mA",
    "capacity_mAh",
    "state_of_charge",
)
DETECTOR_IMAGE_SUFFIXES = {
    ".cbf",
    ".edf",
    ".h5",
    ".hdf5",
    ".img",
    ".mar2300",
    ".mar3450",
    ".nxs",
    ".tif",
    ".tiff",
}
READY_ROUTES = {
    "single_pattern_refinement",
    "sequential_refinement",
    "independent_batch_refinement",
}


def clean_name(value: str) -> str:
    output = []
    for character in value.strip():
        if character.isalnum() or character in "-_.":
            output.append(character)
        elif character in " /\\:;,+()[]{}":
            output.append("_")
    cleaned = "".join(output).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "unnamed"


def require_file(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise SystemExit(f"{label} is missing or empty: {path}")
    return path


def _resolve_inputs(values: Iterable[str | Path], label: str) -> list[Path]:
    return [require_file(value, label) for value in values]


def inspect_sequence_manifest(
    value: str | Path,
    *,
    allow_file_order_only: bool = False,
) -> dict[str, Any]:
    """Inspect routing-critical manifest fields without invoking GSAS-II."""
    path = require_file(value, "sequence manifest")
    errors: list[str] = []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            return {
                "path": str(path),
                "frame_count": 0,
                "metadata_fields": [],
                "varying_coordinates": [],
                "errors": ["manifest has no header"],
            }
        fields = [field.strip() for field in reader.fieldnames]
        missing = {"frame_id", "pattern_path", "order"} - set(fields)
        if missing:
            errors.append(
                "missing required columns: " + ", ".join(sorted(missing))
            )
        for source_row in reader:
            row = {
                (key.strip() if key else ""): (value or "").strip()
                for key, value in source_row.items()
            }
            if any(row.values()):
                rows.append(row)

    seen_ids: set[str] = set()
    seen_orders: set[int] = set()
    pattern_paths: list[str] = []
    for row_index, row in enumerate(rows, start=2):
        frame_id = row.get("frame_id", "")
        if not frame_id:
            errors.append(f"row {row_index}: blank frame_id")
        elif frame_id in seen_ids:
            errors.append(f"row {row_index}: duplicate frame_id {frame_id!r}")
        seen_ids.add(frame_id)

        raw_order = row.get("order", "")
        try:
            order = int(raw_order)
        except ValueError:
            errors.append(f"row {row_index}: noninteger order {raw_order!r}")
        else:
            if order in seen_orders:
                errors.append(f"row {row_index}: duplicate order {order}")
            seen_orders.add(order)

        raw_pattern = row.get("pattern_path", "")
        if not raw_pattern:
            errors.append(f"row {row_index}: blank pattern_path")
            continue
        pattern = Path(raw_pattern).expanduser()
        if not pattern.is_absolute():
            pattern = path.parent / pattern
        pattern = pattern.resolve()
        pattern_paths.append(str(pattern))
        if not pattern.is_file() or pattern.stat().st_size <= 0:
            errors.append(f"row {row_index}: pattern is missing or empty: {pattern}")
        if pattern.suffix.lower() in DETECTOR_IMAGE_SUFFIXES:
            errors.append(
                f"row {row_index}: detector image must be integrated first: {pattern}"
            )

    metadata_fields = [
        field for field in STANDARD_METADATA_FIELDS if field in fields
    ]
    varying_coordinates: list[str] = []
    for field in metadata_fields:
        numeric_values: list[float] = []
        for row_index, row in enumerate(rows, start=2):
            raw_value = row.get(field, "")
            if not raw_value:
                continue
            try:
                numeric_values.append(float(raw_value))
            except ValueError:
                errors.append(
                    f"row {row_index}: nonnumeric {field} {raw_value!r}"
                )
        if len(set(numeric_values)) > 1:
            varying_coordinates.append(field)

    if len(rows) < 2:
        errors.append("sequential refinement requires at least two frames")
    if not varying_coordinates and not allow_file_order_only:
        errors.append(
            "no supported experimental coordinate varies across the frames"
        )

    return {
        "path": str(path),
        "frame_count": len(rows),
        "metadata_fields": metadata_fields,
        "varying_coordinates": varying_coordinates,
        "pattern_paths": pattern_paths,
        "errors": errors,
    }


def _result(
    *,
    classification: str,
    status: str,
    reasons: list[str],
    next_action: str,
    driver: str | None = None,
    target_skill: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "classification": classification,
        "status": status,
        "driver": driver,
        "target_skill": target_skill,
        "reasons": reasons,
        "next_action": next_action,
        "inputs": inputs or {},
        "gsasii_allowed": classification in READY_ROUTES and status == "ready",
        "figure_generation_allowed": False,
    }


def classify_refinement_request(
    *,
    patterns: Iterable[str | Path] = (),
    manifest: str | Path | None = None,
    detector_images: Iterable[str | Path] = (),
    accepted_gpx: Iterable[str | Path] = (),
    intent: str = "refine",
    declared_mode: str = "auto",
    allow_file_order_only: bool = False,
) -> dict[str, Any]:
    """Classify a request before any GSAS-II project is created or modified."""
    allowed_intents = {"auto", "refine", "plot"}
    allowed_modes = {"auto", "single", "sequential", "batch"}
    if intent not in allowed_intents:
        raise ValueError(f"intent must be one of {sorted(allowed_intents)}")
    if declared_mode not in allowed_modes:
        raise ValueError(
            f"declared_mode must be one of {sorted(allowed_modes)}"
        )

    pattern_paths = _resolve_inputs(patterns, "powder pattern")
    detector_paths = _resolve_inputs(detector_images, "detector image")
    gpx_paths = _resolve_inputs(accepted_gpx, "accepted GPX")
    detector_paths.extend(
        path
        for path in pattern_paths
        if path.suffix.lower() in DETECTOR_IMAGE_SUFFIXES
    )
    detector_paths = list(dict.fromkeys(detector_paths))
    input_summary: dict[str, Any] = {
        "patterns": [str(path) for path in pattern_paths],
        "detector_images": [str(path) for path in detector_paths],
        "accepted_gpx": [str(path) for path in gpx_paths],
        "manifest": None,
        "declared_mode": declared_mode,
        "intent": intent,
    }

    if detector_paths:
        return _result(
            classification="detector_integration_required",
            status="blocked",
            reasons=[
                "One or more inputs are two-dimensional detector/image files.",
                "This refinement skill accepts integrated one-dimensional powder patterns only.",
            ],
            next_action=(
                "Integrate the detector frames with calibrated geometry, preserve "
                "the integration provenance, then classify the resulting 1D patterns."
            ),
            inputs=input_summary,
        )

    if intent == "plot":
        target = "rietveld-plotting" if gpx_paths else "separate plotting workflow"
        return _result(
            classification="plotting_handoff",
            status="handoff",
            reasons=[
                "The requested output is a figure rather than numerical refinement."
            ],
            next_action=(
                "Stop the refinement workflow and route the accepted numerical "
                "result or source series to the appropriate plotting skill."
            ),
            target_skill=target,
            inputs=input_summary,
        )

    if gpx_paths:
        return _result(
            classification="existing_project_ambiguous",
            status="needs_clarification",
            reasons=[
                "An existing GPX may be intended for diagnosis, continuation, or plotting.",
                "The requested action is not explicit.",
            ],
            next_action=(
                "Clarify whether to diagnose the project, continue refinement, "
                "or create a read-only figure."
            ),
            inputs=input_summary,
        )

    if manifest is not None and pattern_paths:
        input_summary["manifest"] = str(Path(manifest).expanduser().resolve())
        return _result(
            classification="conflicting_inputs",
            status="blocked",
            reasons=[
                "Patterns were supplied both directly and through a sequence manifest."
            ],
            next_action=(
                "For a sequence, use the manifest as the sole pattern index. "
                "For a single pattern, omit the manifest."
            ),
            inputs=input_summary,
        )

    if manifest is not None:
        manifest_info = inspect_sequence_manifest(
            manifest,
            allow_file_order_only=allow_file_order_only,
        )
        input_summary["manifest"] = manifest_info
        if declared_mode in {"single", "batch"}:
            return _result(
                classification="mode_input_conflict",
                status="blocked",
                reasons=[
                    f"Declared mode {declared_mode!r} conflicts with a sequence manifest."
                ],
                next_action="Use declared mode sequential or remove the manifest.",
                inputs=input_summary,
            )
        if manifest_info["errors"]:
            return _result(
                classification="invalid_sequential_manifest",
                status="blocked",
                reasons=list(manifest_info["errors"]),
                next_action=(
                    "Correct the manifest and integrated pattern inputs before "
                    "creating a GSAS-II project."
                ),
                inputs=input_summary,
            )
        return _result(
            classification="sequential_refinement",
            status="ready",
            reasons=[
                f"Validated {manifest_info['frame_count']} ordered 1D frames.",
                "At least one experimental coordinate varies across the sequence."
                if manifest_info["varying_coordinates"]
                else "File-order-only test mode was explicitly allowed.",
            ],
            next_action=(
                "Run run_sequential_refinement.py using the validated manifest."
            ),
            driver="run_sequential_refinement.py",
            inputs=input_summary,
        )

    if not pattern_paths:
        return _result(
            classification="missing_refinement_input",
            status="blocked",
            reasons=["No integrated powder pattern or sequence manifest was supplied."],
            next_action=(
                "Supply one integrated 1D pattern, or a validated sequence manifest."
            ),
            inputs=input_summary,
        )

    if len(pattern_paths) == 1:
        if declared_mode in {"sequential", "batch"}:
            return _result(
                classification="mode_input_conflict",
                status="blocked",
                reasons=[
                    f"Declared mode {declared_mode!r} requires more than one pattern."
                ],
                next_action=(
                    "Use single mode, or supply the complete sequence/batch inputs."
                ),
                inputs=input_summary,
            )
        return _result(
            classification="single_pattern_refinement",
            status="ready",
            reasons=["Exactly one integrated one-dimensional pattern was supplied."],
            next_action="Run run_staged_refinement.py.",
            driver="run_staged_refinement.py",
            inputs=input_summary,
        )

    if declared_mode == "batch":
        return _result(
            classification="independent_batch_refinement",
            status="ready",
            reasons=[
                f"{len(pattern_paths)} integrated patterns were explicitly declared independent.",
                "They must not share sequential warm-start state.",
            ],
            next_action=(
                "Run run_staged_refinement.py separately for every pattern with "
                "isolated sample IDs and archives."
            ),
            driver="run_staged_refinement.py",
            inputs=input_summary,
        )

    if declared_mode == "sequential":
        return _result(
            classification="sequential_manifest_required",
            status="blocked",
            reasons=[
                "Sequential refinement was declared, but no manifest was supplied."
            ],
            next_action=(
                "Create a manifest with frame_id, order, pattern_path, and a "
                "varying experimental coordinate."
            ),
            inputs=input_summary,
        )

    if declared_mode == "single":
        return _result(
            classification="mode_input_conflict",
            status="blocked",
            reasons=[
                "Single-pattern mode cannot consume multiple patterns in one run."
            ],
            next_action=(
                "Choose independent batch mode or provide a sequential manifest."
            ),
            inputs=input_summary,
        )

    return _result(
        classification="multiple_patterns_ambiguous",
        status="needs_clarification",
        reasons=[
            f"{len(pattern_paths)} integrated patterns were supplied without a manifest.",
            "They may be independent samples or frames from one sequence.",
        ],
        next_action=(
            "Ask whether the files are independent samples or an ordered sequence. "
            "Do not start GSAS-II until the distinction is explicit."
        ),
        inputs=input_summary,
    )


def require_ready_route(
    classification: dict[str, Any],
    expected: str,
) -> None:
    """Stop a driver when the mandatory first-gate classification disagrees."""
    actual = classification.get("classification")
    status = classification.get("status")
    if actual != expected or status != "ready":
        reasons = "; ".join(str(item) for item in classification.get("reasons", []))
        raise SystemExit(
            "Refinement request classification blocked this driver: "
            f"expected={expected}, actual={actual}, status={status}. {reasons}"
        )
