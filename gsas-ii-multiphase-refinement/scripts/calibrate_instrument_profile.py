#!/usr/bin/env python3
"""Calibrate a CW laboratory XRD profile from a standard before QPA."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qpa_core import json_clean, maximum_correlation, sha256_file, write_json_atomic


def required_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def resolve_gsasii_path(value: str | None) -> Path:
    candidates = []
    if value:
        candidates.append(Path(value).expanduser())
    if os.environ.get("GSASII_DIR"):
        candidates.append(Path(os.environ["GSASII_DIR"]).expanduser())
    candidates.extend([Path.home() / "g2main" / "GSAS-II", Path.home() / "GSAS-II"])
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "GSASII" / "GSASIIscriptable.py").is_file():
            return resolved
    raise FileNotFoundError("GSAS-II not found; set GSASII_DIR or pass --gsasii-path")


def clear_all_profile_flags(histogram: Any) -> None:
    histogram.clear_refinements(
        {"Instrument Parameters": ["Zero", "U", "V", "W", "X", "Y", "Z", "SH/L"]}
    )


def refine_until(
    project: Any, *, max_passes: int, shift_limit: float
) -> tuple[int, dict[str, Any]]:
    r_values: dict[str, Any] = {}
    for index in range(max_passes):
        project.refine()
        project.save()
        r_values = project.data.get("Covariance", {}).get("data", {}).get("Rvals", {})
        shift = r_values.get("Max shft/sig")
        if (
            r_values.get("converged", False)
            and shift is not None
            and abs(float(shift)) <= shift_limit
        ):
            return index + 1, r_values
    return max_passes, r_values


def profile_width_check(instrument: dict[str, Any], limits: tuple[float, float]) -> dict[str, Any]:
    values = {name: float(instrument[name][1]) for name in ("U", "V", "W", "X", "Y")}
    points = []
    for two_theta in limits:
        theta = math.radians(two_theta / 2.0)
        tangent = math.tan(theta)
        gaussian = values["U"] * tangent**2 + values["V"] * tangent + values["W"]
        lorentzian = values["X"] / math.cos(theta) + values["Y"] * tangent
        points.append(
            {
                "two_theta": two_theta,
                "gaussian_width_squared": gaussian,
                "lorentzian_width": lorentzian,
            }
        )
    return {
        "points": points,
        "positive": all(
            item["gaussian_width_squared"] > 0.0 and item["lorentzian_width"] >= 0.0
            for item in points
        ),
    }


def candidate_record(
    project: Any,
    histogram: Any,
    *,
    name: str,
    parent: str | None,
    releases: list[str],
    passes: int,
    gpx_path: Path,
    limits: tuple[float, float],
) -> dict[str, Any]:
    covariance = project.data.get("Covariance", {}).get("data", {})
    r_values = covariance.get("Rvals", {})
    instrument = histogram.data["Instrument Parameters"][0]
    correlation = maximum_correlation(
        list(covariance.get("varyList", [])), covariance.get("covMatrix", [])
    )
    widths = profile_width_check(instrument, limits)
    converged = bool(r_values.get("converged", False))
    svd_count = int(r_values.get("SVD0", 0) or 0)
    shift = r_values.get("Max shft/sig")
    safe = (
        converged
        and svd_count == 0
        and shift is not None
        and abs(float(shift)) <= 0.01
        and correlation.get("absolute") is not None
        and float(correlation["absolute"]) < 0.95
        and widths["positive"]
    )
    return json_clean(
        {
            "candidate": name,
            "parent": parent,
            "releases": releases,
            "refinement_passes": passes,
            "safe": safe,
            "metrics": {
                "Rwp_percent": r_values.get("Rwp"),
                "GOF": r_values.get("GOF"),
                "converged": converged,
                "svd_count": svd_count,
                "max_shift_over_esd": shift,
                "maximum_correlation": correlation,
            },
            "instrument": {
                key: float(value[1]) if isinstance(value, list) and len(value) > 1 else value
                for key, value in instrument.items()
                if key in {"Lam1", "Lam2", "I(L2)/I(L1)", "Zero", "U", "V", "W", "X", "Y", "SH/L"}
            },
            "profile_width_check": widths,
            "gpx": {"path": str(gpx_path), "sha256": sha256_file(gpx_path)},
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard-pattern", required=True)
    parser.add_argument("--standard-cif", required=True)
    parser.add_argument("--seed-instrument", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--standard-name", default="instrument_standard")
    parser.add_argument("--pattern-format", default="Topas xye")
    parser.add_argument("--background-order", type=int, default=10)
    parser.add_argument("--two-theta-min", type=float, default=10.0)
    parser.add_argument("--two-theta-max", type=float, default=145.0)
    parser.add_argument("--max-refinement-passes", type=int, default=12)
    parser.add_argument("--max-shift-over-esd", type=float, default=0.01)
    parser.add_argument("--gsasii-path")
    args = parser.parse_args()
    if not 2 <= args.background_order <= 20:
        parser.error("--background-order must be between 2 and 20")
    if args.two_theta_min >= args.two_theta_max:
        parser.error("two-theta minimum must be less than maximum")
    if args.max_refinement_passes < 1 or args.max_shift_over_esd <= 0.0:
        parser.error("refinement pass and shift limits must be positive")

    pattern = required_file(args.standard_pattern, "standard pattern")
    cif = required_file(args.standard_cif, "standard CIF")
    seed_instrument = required_file(args.seed_instrument, "seed instrument")
    gsasii_path = resolve_gsasii_path(args.gsasii_path)
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    candidates_dir = output_dir / "candidates"
    candidates_dir.mkdir()
    limits = (args.two_theta_min, args.two_theta_max)
    script_path = Path(__file__).resolve()
    core_path = script_path.with_name("qpa_core.py")
    protocol = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "answer-independent instrument-profile calibration before QPA",
        "inputs": {
            "standard_pattern": {"path": str(pattern), "sha256": sha256_file(pattern)},
            "standard_cif": {"path": str(cif), "sha256": sha256_file(cif)},
            "seed_instrument": {"path": str(seed_instrument), "sha256": sha256_file(seed_instrument)},
        },
        "code": {
            "driver": {"path": str(script_path), "sha256": sha256_file(script_path)},
            "qpa_core": {"path": str(core_path), "sha256": sha256_file(core_path)},
        },
        "settings": {
            "background_order": args.background_order,
            "limits": limits,
            "max_refinement_passes": args.max_refinement_passes,
            "max_shift_over_esd": args.max_shift_over_esd,
            "profile_sequence": ["locked", "zero", "w", "uvw", "uvwxy"],
            "selection": "lowest GOF among converged, nonsingular, positive-width candidates with max correlation <0.95 and max shift/esd <=0.01",
        },
    }
    write_json_atomic(output_dir / "protocol_manifest.json", protocol)

    sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIscriptable as G2sc
    from GSASII import GSASIIpath

    G2sc.SetPrintLevel("warn")
    initial = candidates_dir / "00_initial.gpx"
    project = G2sc.G2Project(newgpx=str(initial))
    histogram = project.add_powder_histogram(
        str(pattern), str(seed_instrument), fmthint=args.pattern_format
    )
    phase = project.add_phase(
        str(cif), phasename=args.standard_name, histograms=[histogram]
    )
    histogram.set_refinements({"Limits": list(limits)})
    histogram.clear_refinements(
        {
            "Sample Parameters": [
                "Scale", "Shift", "DisplaceX", "DisplaceY", "Transparency",
                "Absorption", "SurfRoughA", "SurfRoughB",
            ],
            "Background": True,
        }
    )
    clear_all_profile_flags(histogram)
    histogram.set_refinements(
        {
            "Background": {
                "type": "chebyschev-1",
                "no. coeffs": args.background_order,
                "refine": True,
            },
            "Sample Parameters": ["Scale"],
        }
    )
    phase.clear_HAP_refinements(
        {"Scale": True, "Mustrain": True, "Size": True, "Pref.Ori.": True, "HStrain": True},
        [histogram],
    )
    phase.HAPvalue("Scale", 1.0, [histogram])
    phase.set_refinements({"Cell": False, "Atoms": {"all": ""}})
    project.set_Controls("cycles", 12)
    project.save(str(initial))

    stages = [
        ("01_locked", "00_initial", []),
        ("02_zero", "01_locked", ["Zero"]),
        ("03_w", "02_zero", ["W"]),
        ("04_uvw", "03_w", ["U", "V", "W"]),
        ("05_uvwxy", "04_uvw", ["U", "V", "W", "X", "Y"]),
    ]
    paths = {"00_initial": initial}
    candidates = []
    for name, parent, releases in stages:
        destination = candidates_dir / f"{name}.gpx"
        shutil.copy2(paths[parent], destination)
        candidate_project = G2sc.G2Project(str(destination))
        candidate_histogram = candidate_project.histograms()[0]
        clear_all_profile_flags(candidate_histogram)
        if releases:
            candidate_histogram.set_refinements({"Instrument Parameters": releases})
        passes, _ = refine_until(
            candidate_project,
            max_passes=args.max_refinement_passes,
            shift_limit=args.max_shift_over_esd,
        )
        candidate_project.save(str(destination))
        record = candidate_record(
            candidate_project,
            candidate_histogram,
            name=name,
            parent=parent,
            releases=releases,
            passes=passes,
            gpx_path=destination,
            limits=limits,
        )
        candidates.append(record)
        paths[name] = destination
        write_json_atomic(candidates_dir / f"{name}.json", record)

    safe = [item for item in candidates if item["safe"]]
    if not safe:
        selected = None
        status = "fail"
    else:
        selected = min(safe, key=lambda item: (float(item["metrics"]["GOF"]), item["candidate"]))
        status = "pass"
        selected_project = G2sc.G2Project(str(paths[selected["candidate"]]))
        selected_project.histograms()[0].SaveProfile(str(output_dir / "calibrated.instprm"))
    summary = {
        "schema_version": 1,
        "status": status,
        "real_gsasii": True,
        "gsasii": {
            "path": str(gsasii_path),
            "version_number": GSASIIpath.GetVersionNumber(),
            "python": sys.version,
        },
        "protocol_manifest": str(output_dir / "protocol_manifest.json"),
        "selected_candidate": selected["candidate"] if selected else None,
        "selected_profile": str(output_dir / "calibrated.instprm") if selected else None,
        "selected_profile_artifact": (
            {
                "path": str(output_dir / "calibrated.instprm"),
                "sha256": sha256_file(output_dir / "calibrated.instprm"),
            }
            if selected
            else None
        ),
        "candidates": candidates,
        "limitations": [
            "standard-cell dimensions are fixed so Zero is identifiable",
            "profile is calibrated from one standard pattern and must not be generalized across instruments",
            "whole-pattern Rwp can retain intensity-model mismatch; selection additionally gates convergence, SVD, correlation and positive widths",
            "no figure generated",
        ],
    }
    write_json_atomic(output_dir / "calibration_summary.json", summary)
    print(json.dumps(json_clean({"status": status, "selected": summary["selected_candidate"], "summary": str(output_dir / "calibration_summary.json")}), indent=2))
    return 0 if status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
