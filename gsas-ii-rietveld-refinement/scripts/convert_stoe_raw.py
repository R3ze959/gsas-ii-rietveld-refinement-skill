#!/usr/bin/env python3
"""Convert validated STOE WinXPOW ``RAW_1.06Powdat`` frames to XYE.

This reader is intentionally narrow. It accepts the WinXPOW layout observed in
the public University of St Andrews operando dataset and rejects unknown,
truncated, internally inconsistent, or zero-intensity records. It does not
guess offsets or repair damaged acquisitions.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from typing import Iterable


MAGIC = b"RAW_1.06Powdat"
FRAME_HEADER_OFFSET = 0x800
DATA_OFFSET = 0xA00
TIMESTAMP_FORMAT = "%d-%b-%y %H:%M"


@dataclass(frozen=True)
class StoeFrame:
    source: Path
    start_time: datetime
    end_time: datetime
    two_theta_start: float
    two_theta_end: float
    step: float
    intensity: tuple[int, ...]
    duplicate_record_verified: bool

    @property
    def point_count(self) -> int:
        return len(self.intensity)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(raw: bytes, offset: int, label: str) -> datetime:
    text = raw[offset : offset + 16].split(b"\0", 1)[0].decode(
        "ascii", errors="strict"
    )
    try:
        return datetime.strptime(text, TIMESTAMP_FORMAT)
    except ValueError as exc:
        raise ValueError(f"Invalid STOE {label} timestamp: {text!r}") from exc


def _read_record(raw: bytes, header_offset: int, data_offset: int) -> dict:
    if len(raw) < header_offset + 0x48:
        raise ValueError("STOE frame is truncated before its range header")
    point_count = struct.unpack_from("<H", raw, header_offset + 0x22)[0]
    start = struct.unpack_from("<f", raw, header_offset + 0x2C)[0]
    end = struct.unpack_from("<f", raw, header_offset + 0x34)[0]
    step = struct.unpack_from("<f", raw, header_offset + 0x3C)[0]
    if point_count < 20:
        raise ValueError(f"Implausible STOE point count: {point_count}")
    if not all(math.isfinite(value) for value in (start, end, step)):
        raise ValueError("Non-finite STOE angular metadata")
    if start < 0 or end <= start or step <= 0:
        raise ValueError(
            f"Implausible STOE range: start={start}, end={end}, step={step}"
        )
    expected_end = start + (point_count - 1) * step
    tolerance = max(2e-5, abs(step) * 2e-3)
    if abs(expected_end - end) > tolerance:
        raise ValueError(
            "STOE point count/range mismatch: "
            f"expected end {expected_end:.7g}, recorded {end:.7g}"
        )
    required = data_offset + point_count * 4
    if len(raw) < required:
        raise ValueError(
            f"STOE intensity record is truncated: need {required} bytes, "
            f"found {len(raw)}"
        )
    intensity = struct.unpack_from(f"<{point_count}I", raw, data_offset)
    return {
        "point_count": point_count,
        "start": float(start),
        "end": float(end),
        "step": float(step),
        "intensity": intensity,
    }


def duplicate_record_offsets(point_count: int) -> tuple[int, int]:
    """Return the 0x200-aligned duplicate header and its data offset."""
    first_record_end = DATA_OFFSET + point_count * 4
    header_offset = (first_record_end + 0x1FF) & ~0x1FF
    return header_offset, header_offset + 0x200


def read_stoe_frame(path: Path) -> StoeFrame:
    path = path.expanduser().resolve()
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError(f"Unsupported STOE header in {path}: expected {MAGIC!r}")
    start_time = _timestamp(raw, FRAME_HEADER_OFFSET, "start")
    end_time = _timestamp(raw, FRAME_HEADER_OFFSET + 0x10, "end")
    if end_time < start_time:
        raise ValueError("STOE end timestamp precedes its start timestamp")
    first = _read_record(raw, FRAME_HEADER_OFFSET, DATA_OFFSET)
    intensity = first["intensity"]
    if not any(intensity):
        raise ValueError("STOE frame contains only zero intensity values")

    duplicate_verified = False
    second_header_offset, second_data_offset = duplicate_record_offsets(
        first["point_count"]
    )
    second_header = raw[
        second_header_offset : second_header_offset + 16
    ].split(b"\0", 1)[0]
    has_second_record = bool(
        re.fullmatch(
            rb"\d{2}-[A-Za-z]{3}-\d{2} \d{2}:\d{2}", second_header
        )
    )
    if has_second_record:
        second_start_time = _timestamp(raw, second_header_offset, "duplicate start")
        second_end_time = _timestamp(
            raw, second_header_offset + 0x10, "duplicate end"
        )
        if second_end_time < second_start_time:
            raise ValueError(
                "Duplicated STOE end timestamp precedes its start timestamp"
            )
        second = _read_record(raw, second_header_offset, second_data_offset)
        metadata = ("point_count", "start", "end", "step")
        if any(first[key] != second[key] for key in metadata):
            raise ValueError("Duplicated STOE range metadata does not match")
        if first["intensity"] != second["intensity"]:
            raise ValueError("Duplicated STOE intensity records do not match")
        duplicate_verified = True
        start_time = min(start_time, second_start_time)
        end_time = max(end_time, second_end_time)

    return StoeFrame(
        source=path,
        start_time=start_time,
        end_time=end_time,
        two_theta_start=first["start"],
        two_theta_end=first["end"],
        step=first["step"],
        intensity=tuple(intensity),
        duplicate_record_verified=duplicate_verified,
    )


def write_xye(frame: StoeFrame, output: Path) -> dict:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# STOE WinXPOW RAW_1.06Powdat converted without smoothing\n")
        stream.write(f"# source_sha256={sha256_file(frame.source)}\n")
        stream.write(
            f"# start={frame.start_time.isoformat()} end={frame.end_time.isoformat()}\n"
        )
        stream.write("# columns: 2theta_deg intensity_counts sigma_counts\n")
        for index, counts in enumerate(frame.intensity):
            angle = frame.two_theta_start + index * frame.step
            sigma = math.sqrt(max(counts, 1))
            stream.write(f"{angle:.6f} {counts:d} {sigma:.8g}\n")
    temporary.replace(output)
    return {
        "path": str(output),
        "sha256": sha256_file(output),
        "bytes": output.stat().st_size,
    }


def natural_key(path: Path) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    )


def convert_series(
    inputs: Iterable[Path],
    output_directory: Path,
    *,
    skip_invalid: bool,
) -> dict:
    output_directory = output_directory.expanduser().resolve()
    converted: list[dict] = []
    rejected: list[dict] = []
    reference_grid: tuple[int, float, float, float] | None = None
    ordered = sorted((path.expanduser().resolve() for path in inputs), key=natural_key)
    for source in ordered:
        try:
            frame = read_stoe_frame(source)
            grid = (
                frame.point_count,
                frame.two_theta_start,
                frame.two_theta_end,
                frame.step,
            )
            if reference_grid is None:
                reference_grid = grid
            elif any(abs(left - right) > 2e-5 for left, right in zip(grid, reference_grid)):
                raise ValueError(
                    f"Incompatible STOE grid {grid}; expected {reference_grid}"
                )
            output = output_directory / f"{source.name}.xye"
            output_record = write_xye(frame, output)
            converted.append(
                {
                    "frame_id": source.name,
                    "order": len(converted),
                    "pattern_path": output_record["path"],
                    "source": {
                        "path": str(source),
                        "sha256": sha256_file(source),
                        "bytes": source.stat().st_size,
                    },
                    "output": output_record,
                    "start_time": frame.start_time.isoformat(),
                    "end_time": frame.end_time.isoformat(),
                    "midpoint_time": (
                        frame.start_time + (frame.end_time - frame.start_time) / 2
                    ).isoformat(),
                    "point_count": frame.point_count,
                    "two_theta_start": frame.two_theta_start,
                    "two_theta_end": frame.two_theta_end,
                    "step": frame.step,
                    "duplicate_record_verified": frame.duplicate_record_verified,
                }
            )
        except (OSError, UnicodeError, ValueError, struct.error) as exc:
            rejected.append({"path": str(source), "reason": str(exc)})
            if not skip_invalid:
                raise
    if not converted:
        raise ValueError("No valid STOE frames were converted")

    first_start = datetime.fromisoformat(converted[0]["start_time"])
    frame_index = output_directory / "frame_index.csv"
    temporary_index = frame_index.with_name(f".{frame_index.name}.tmp")
    with temporary_index.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=(
                "frame_id",
                "order",
                "pattern_path",
                "time_s",
                "start_time",
                "end_time",
            ),
        )
        writer.writeheader()
        for record in converted:
            midpoint = datetime.fromisoformat(record["midpoint_time"])
            writer.writerow(
                {
                    "frame_id": record["frame_id"],
                    "order": record["order"],
                    "pattern_path": record["pattern_path"],
                    "time_s": f"{(midpoint - first_start).total_seconds():.6f}",
                    "start_time": record["start_time"],
                    "end_time": record["end_time"],
                }
            )
    temporary_index.replace(frame_index)

    audit = {
        "schema_version": 1,
        "format": "STOE WinXPOW RAW_1.06Powdat",
        "conversion": "unsigned 32-bit counts to XYE; sigma=sqrt(max(counts,1))",
        "smoothing_performed": False,
        "background_subtraction_performed": False,
        "converted_count": len(converted),
        "rejected_count": len(rejected),
        "reference_grid": {
            "point_count": reference_grid[0],
            "two_theta_start": reference_grid[1],
            "two_theta_end": reference_grid[2],
            "step": reference_grid[3],
        },
        "frame_index": {
            "path": str(frame_index),
            "sha256": sha256_file(frame_index),
        },
        "frames": converted,
        "rejected": rejected,
    }
    audit_path = output_directory / "stoe_conversion_audit.json"
    temporary_audit = audit_path.with_name(f".{audit_path.name}.tmp")
    temporary_audit.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_audit.replace(audit_path)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", action="append", help="STOE frame; repeat as needed")
    group.add_argument("--input-directory", help="Directory containing STOE frames")
    parser.add_argument("--glob", default="*.???", help="Directory mode glob")
    parser.add_argument("--output-directory", required=True)
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Record invalid/incomplete frames in the audit instead of stopping",
    )
    args = parser.parse_args()
    if args.input_directory:
        inputs = list(Path(args.input_directory).expanduser().glob(args.glob))
    else:
        inputs = [Path(value) for value in args.input]
    audit = convert_series(
        inputs,
        Path(args.output_directory),
        skip_invalid=args.skip_invalid,
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
