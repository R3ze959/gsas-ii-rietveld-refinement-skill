#!/usr/bin/env python3
"""Classify a powder-refinement request before any GSAS-II action."""

from __future__ import annotations

import argparse
import json

from refinement_core import classify_refinement_request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="Integrated one-dimensional powder pattern; repeat as needed",
    )
    parser.add_argument("--manifest", help="CSV manifest for an ordered sequence")
    parser.add_argument(
        "--detector-image",
        action="append",
        default=[],
        help="Two-dimensional detector frame; repeat as needed",
    )
    parser.add_argument(
        "--accepted-gpx",
        action="append",
        default=[],
        help="Existing accepted GSAS-II project",
    )
    parser.add_argument(
        "--intent",
        choices=("auto", "refine", "plot"),
        default="refine",
    )
    parser.add_argument(
        "--declared-mode",
        choices=("auto", "single", "sequential", "batch"),
        default="auto",
    )
    parser.add_argument(
        "--allow-file-order-only",
        action="store_true",
        help="Allow a sequence with no varying experimental coordinate for tests",
    )
    args = parser.parse_args()
    result = classify_refinement_request(
        patterns=args.pattern,
        manifest=args.manifest,
        detector_images=args.detector_image,
        accepted_gpx=args.accepted_gpx,
        intent=args.intent,
        declared_mode=args.declared_mode,
        allow_file_order_only=args.allow_file_order_only,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ready", "handoff"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
