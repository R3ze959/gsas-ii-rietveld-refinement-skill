from __future__ import annotations

from datetime import datetime
import struct
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

from convert_stoe_raw import (  # noqa: E402
    DATA_OFFSET,
    FRAME_HEADER_OFFSET,
    MAGIC,
    convert_series,
    duplicate_record_offsets,
    read_stoe_frame,
)


def synthetic_frame(
    path: Path,
    *,
    zero: bool = False,
    mismatch: bool = False,
    duplicate: bool = True,
    count: int = 20,
) -> None:
    start = 4.0
    step = 0.015
    end = start + (count - 1) * step
    second_header, second_data = duplicate_record_offsets(count)
    size = second_data + count * 4 if duplicate else DATA_OFFSET + count * 4
    raw = bytearray(size)
    raw[: len(MAGIC)] = MAGIC
    values = [0 if zero else 100 + index for index in range(count)]
    records = [(FRAME_HEADER_OFFSET, DATA_OFFSET)]
    if duplicate:
        records.append((second_header, second_data))
    for header, data in records:
        raw[header : header + 16] = b"01-Jan-25 00:00\0"
        raw[header + 0x10 : header + 0x20] = b"01-Jan-25 00:10\0"
        struct.pack_into("<H", raw, header + 0x22, count)
        struct.pack_into("<f", raw, header + 0x2C, start)
        struct.pack_into("<f", raw, header + 0x34, end)
        struct.pack_into("<f", raw, header + 0x3C, step)
        struct.pack_into(f"<{count}I", raw, data, *values)
    if mismatch:
        struct.pack_into("<I", raw, second_data, 999)
    path.write_bytes(raw)


class StoeConversionTests(unittest.TestCase):
    def test_valid_duplicate_record_is_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "frame.002"
            synthetic_frame(source)
            frame = read_stoe_frame(source)
        self.assertEqual(20, frame.point_count)
        self.assertEqual(datetime(2025, 1, 1, 0, 0), frame.start_time)
        self.assertTrue(frame.duplicate_record_verified)
        self.assertEqual(100, frame.intensity[0])

    def test_duplicate_intensity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "frame.002"
            synthetic_frame(source, mismatch=True)
            with self.assertRaisesRegex(ValueError, "do not match"):
                read_stoe_frame(source)

    def test_large_single_record_is_not_mistaken_for_a_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "static.raw"
            synthetic_frame(source, duplicate=False, count=2300)
            frame = read_stoe_frame(source)
        self.assertEqual(2300, frame.point_count)
        self.assertFalse(frame.duplicate_record_verified)

    def test_zero_only_frame_is_rejected_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            synthetic_frame(root / "frame.002")
            synthetic_frame(root / "frame.003", zero=True)
            audit = convert_series(
                [root / "frame.002", root / "frame.003"],
                root / "converted",
                skip_invalid=True,
            )
        self.assertEqual(1, audit["converted_count"])
        self.assertEqual(1, audit["rejected_count"])
        self.assertIn("only zero", audit["rejected"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
