#!/usr/bin/env python3
"""Build a deterministic operando manifest from a frame index and metadata CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


CORE_FIELDS = ("frame_id", "order", "pattern_path")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        fields = [field.strip() for field in reader.fieldnames]
        rows = [
            {
                (key.strip() if key else ""): (value or "").strip()
                for key, value in row.items()
            }
            for row in reader
            if any((value or "").strip() for value in row.values())
        ]
    return fields, rows


def _numeric(value: str, *, label: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be numeric: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite: {value!r}")
    return result


def _normalized_key(value: str, join_on: str) -> str:
    if join_on == "order":
        try:
            return str(int(value))
        except ValueError as exc:
            raise ValueError(f"order join key must be an integer: {value!r}") from exc
    return value.strip()


def merge_exact(
    frames: list[dict[str, str]],
    metadata: list[dict[str, str]],
    *,
    join_on: str,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    lookup: dict[str, dict[str, str]] = {}
    for row in metadata:
        key = _normalized_key(row.get(join_on, ""), join_on)
        if not key:
            raise ValueError(f"Metadata row has no {join_on}")
        if key in lookup:
            raise ValueError(f"Duplicate metadata {join_on}: {key}")
        lookup[key] = row
    merged = []
    unmatched = []
    used = set()
    for frame in frames:
        key = _normalized_key(frame.get(join_on, ""), join_on)
        metadata_row = lookup.get(key)
        if metadata_row is None:
            unmatched.append(frame.get("frame_id", key))
            continue
        output = dict(frame)
        for field, value in metadata_row.items():
            if field == join_on or not value:
                continue
            if field in output and output[field] and output[field] != value:
                raise ValueError(
                    f"Conflicting nonblank field {field!r} for {join_on}={key}"
                )
            output[field] = value
        merged.append(output)
        used.add(key)
    unused = sorted(set(lookup) - used)
    return merged, {
        "mode": f"exact_{join_on}",
        "unmatched_frame_ids": unmatched,
        "unused_metadata_keys": unused,
        "matched_count": len(merged),
    }


def merge_nearest_time(
    frames: list[dict[str, str]],
    metadata: list[dict[str, str]],
    *,
    frame_time_column: str,
    metadata_time_column: str,
    maximum_delta_s: float,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    timed_metadata = [
        (
            _numeric(
                row.get(metadata_time_column, ""),
                label=f"metadata {metadata_time_column}",
            ),
            index,
            row,
        )
        for index, row in enumerate(metadata)
    ]
    if not timed_metadata:
        raise ValueError("Metadata CSV has no rows")
    timed_metadata.sort(key=lambda item: (item[0], item[1]))
    frame_times = [
        _numeric(
            frame.get(frame_time_column, ""),
            label=f"frame {frame_time_column}",
        )
        for frame in frames
    ]
    if any(right <= left for left, right in zip(frame_times, frame_times[1:])):
        raise ValueError(
            f"Frame {frame_time_column} values must increase strictly in frame order"
        )
    deltas = []
    merged = []
    unmatched = []
    matches = []
    used_metadata_indices: set[int] = set()
    last_metadata_position = -1
    for frame, frame_time in zip(frames, frame_times):
        candidates = [
            (position, item)
            for position, item in enumerate(timed_metadata)
            if position > last_metadata_position
        ]
        if not candidates:
            unmatched.append(frame.get("frame_id", str(frame_time)))
            continue
        metadata_position, (
            metadata_time,
            metadata_index,
            metadata_row,
        ) = min(
            candidates,
            key=lambda candidate: (
                abs(candidate[1][0] - frame_time),
                candidate[1][0],
                candidate[1][1],
            ),
        )
        delta = metadata_time - frame_time
        if abs(delta) > maximum_delta_s:
            unmatched.append(frame.get("frame_id", str(frame_time)))
            continue
        output = dict(frame)
        for field, value in metadata_row.items():
            if field == metadata_time_column or not value:
                continue
            if field in output and output[field] and output[field] != value:
                raise ValueError(
                    f"Conflicting nonblank field {field!r} near time {frame_time}"
                )
            output[field] = value
        output["sync_delta_s"] = f"{delta:.12g}"
        merged.append(output)
        deltas.append(delta)
        last_metadata_position = metadata_position
        used_metadata_indices.add(metadata_index)
        matches.append(
            {
                "frame_id": frame.get("frame_id", ""),
                "frame_order": int(frame.get("order", len(matches))),
                "frame_time_s": frame_time,
                "metadata_row_index": metadata_index,
                "metadata_time_s": metadata_time,
                "sync_delta_s": delta,
            }
        )
    return merged, {
        "mode": "nearest_time",
        "frame_time_column": frame_time_column,
        "metadata_time_column": metadata_time_column,
        "maximum_delta_s": maximum_delta_s,
        "unmatched_frame_ids": unmatched,
        "unused_metadata_row_indices": sorted(
            set(range(len(metadata))) - used_metadata_indices
        ),
        "matched_count": len(merged),
        "matches": matches,
        "maximum_absolute_sync_delta_s": (
            max(abs(value) for value in deltas) if deltas else None
        ),
    }


def build_manifest(
    *,
    frame_index: Path,
    metadata_csv: Path,
    output: Path,
    join_on: str,
    frame_time_column: str,
    metadata_time_column: str,
    maximum_delta_s: float | None,
) -> dict[str, Any]:
    frame_index = frame_index.expanduser().resolve()
    metadata_csv = metadata_csv.expanduser().resolve()
    output = output.expanduser().resolve()
    frame_fields, frames = read_rows(frame_index)
    metadata_fields, metadata = read_rows(metadata_csv)
    missing = set(CORE_FIELDS) - set(frame_fields)
    if missing:
        raise ValueError(
            "Frame index is missing required columns: "
            + ", ".join(sorted(missing))
        )
    ids = [row["frame_id"] for row in frames]
    orders = [_normalized_key(row["order"], "order") for row in frames]
    if len(ids) != len(set(ids)) or len(orders) != len(set(orders)):
        raise ValueError("Frame index has duplicate frame_id or order values")
    frames.sort(key=lambda row: int(row["order"]))
    for row in frames:
        pattern = Path(row["pattern_path"]).expanduser()
        if not pattern.is_absolute():
            pattern = frame_index.parent / pattern
        if not pattern.is_file() or pattern.stat().st_size <= 0:
            raise ValueError(f"Pattern is missing or empty: {pattern}")
        row["pattern_path"] = str(pattern.resolve())
    if join_on in {"frame_id", "order"}:
        if join_on not in metadata_fields:
            raise ValueError(f"Metadata CSV has no {join_on} column")
        merged, audit = merge_exact(frames, metadata, join_on=join_on)
    else:
        if maximum_delta_s is None or maximum_delta_s <= 0:
            raise ValueError("nearest-time join requires a positive maximum delta")
        merged, audit = merge_nearest_time(
            frames,
            metadata,
            frame_time_column=frame_time_column,
            metadata_time_column=metadata_time_column,
            maximum_delta_s=maximum_delta_s,
        )
    if audit["unmatched_frame_ids"]:
        raise ValueError(
            "Metadata synchronization left unmatched frames: "
            + ", ".join(audit["unmatched_frame_ids"])
        )
    merged.sort(key=lambda row: int(row["order"]))
    fields = list(CORE_FIELDS)
    for field in frame_fields + metadata_fields + ["sync_delta_s"]:
        if field not in fields and any(row.get(field, "") for row in merged):
            fields.append(field)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)
    temporary.replace(output)
    audit.update(
        {
            "schema_version": 1,
            "frame_index": str(frame_index),
            "metadata_csv": str(metadata_csv),
            "output_manifest": str(output),
            "input_files": {
                "frame_index": {
                    "path": str(frame_index),
                    "sha256": sha256_file(frame_index),
                    "bytes": frame_index.stat().st_size,
                },
                "metadata_csv": {
                    "path": str(metadata_csv),
                    "sha256": sha256_file(metadata_csv),
                    "bytes": metadata_csv.stat().st_size,
                },
            },
            "output_manifest_sha256": sha256_file(output),
            "output_manifest_bytes": output.stat().st_size,
            "frame_count": len(merged),
            "interpolation_performed": False,
        }
    )
    audit_path = output.with_suffix(output.suffix + ".sync.json")
    temporary_audit = audit_path.with_name(f".{audit_path.name}.tmp")
    temporary_audit.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_audit.replace(audit_path)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-index", required=True)
    parser.add_argument("--metadata-csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--join-on",
        choices=("frame_id", "order", "nearest-time"),
        default="frame_id",
    )
    parser.add_argument("--frame-time-column", default="time_s")
    parser.add_argument("--metadata-time-column", default="time_s")
    parser.add_argument("--maximum-delta-s", type=float)
    args = parser.parse_args()
    audit = build_manifest(
        frame_index=Path(args.frame_index).expanduser().resolve(),
        metadata_csv=Path(args.metadata_csv).expanduser().resolve(),
        output=Path(args.output).expanduser().resolve(),
        join_on=args.join_on,
        frame_time_column=args.frame_time_column,
        metadata_time_column=args.metadata_time_column,
        maximum_delta_s=args.maximum_delta_s,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
