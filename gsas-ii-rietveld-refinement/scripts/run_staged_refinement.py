#!/usr/bin/env python3
"""Run a deterministic, branched GSAS-II powder refinement sequence."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from refinement_audit import (
    candidate_safety_errors,
    collect_candidate,
    default_data_root,
    resolve_gsasii_path,
    sha256,
    write_json_atomic,
)


DEFAULT_STAGING_ROOT = default_data_root(
    "GSASII_REFINEMENT_STAGING", "GSAS-II_refinement_staging"
)


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


def require_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.stat().st_size <= 0:
        raise SystemExit(f"{label} is missing or empty: {path}")
    return path


def configure_flags(
    histogram: Any,
    phase: Any,
    *,
    refine_cell: bool,
    refine_zero: bool,
    profile_mode: str = "locked",
) -> None:
    """Set all geometry/profile flags explicitly; never inherit them silently."""
    phase.set_refinements({"Cell": bool(refine_cell)})
    phase.set_refinements({"Atoms": {"all": ""}})
    instrument = histogram.data["Instrument Parameters"][0]
    for key, value in instrument.items():
        if isinstance(value, list) and len(value) > 2 and isinstance(value[2], bool):
            value[2] = False
    if "Zero" in instrument:
        instrument["Zero"][2] = bool(refine_zero)
    if profile_mode == "w":
        if "W" not in instrument:
            raise ValueError("Instrument model has no W term")
        instrument["W"][2] = True
    elif profile_mode == "uvw":
        missing = [key for key in ("U", "V", "W") if key not in instrument]
        if missing:
            raise ValueError(f"Instrument model lacks profile terms: {missing}")
        for key in ("U", "V", "W"):
            instrument[key][2] = True
    elif profile_mode != "locked":
        raise ValueError(f"Unknown profile mode: {profile_mode}")


def configure_baseline(
    project: Any,
    histogram: Any,
    phase: Any,
    *,
    background_order: int,
    limits: tuple[float, float] | None,
) -> None:
    if limits is not None:
        histogram.set_refinements({"Limits": [limits[0], limits[1]]})
    histogram.clear_refinements(
        {
            "Sample Parameters": [
                "Scale",
                "Shift",
                "DisplaceX",
                "DisplaceY",
                "Transparency",
                "Absorption",
                "SurfRoughA",
                "SurfRoughB",
            ]
        }
    )
    histogram.clear_refinements({"Background": True})
    histogram.set_refinements(
        {
            "Background": {
                "type": "chebyschev-1",
                "no. coeffs": background_order,
                "refine": True,
            }
        }
    )
    phase.clear_HAP_refinements(
        {
            "Scale": True,
            "Mustrain": True,
            "Size": True,
            "Pref.Ori.": True,
            "HStrain": True,
        },
        [histogram],
    )
    phase.set_HAP_refinements({"Scale": True}, [histogram])
    configure_flags(
        histogram,
        phase,
        refine_cell=False,
        refine_zero=False,
        profile_mode="locked",
    )
    project.set_Controls("cycles", 8)


def copy_project(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Candidate output already exists: {destination}")
    shutil.copy2(source, destination)


def build_plan(profile_mode: str, profile_base: str) -> list[dict[str, Any]]:
    plan = [
        {
            "name": "01_scale_background",
            "parent": "00_unrefined",
            "releases": ["scale", "background"],
        },
        {
            "name": "02_cell_only",
            "parent": "01_scale_background",
            "releases": ["cell"],
        },
        {
            "name": "03_zero_only",
            "parent": "01_scale_background",
            "releases": ["zero"],
        },
        {
            "name": "04_cell_zero_simultaneous",
            "parent": "01_scale_background",
            "releases": ["cell", "zero"],
        },
        {
            "name": "05_cell_then_zero",
            "parent": "02_cell_only",
            "releases": ["cell", "zero"],
        },
        {
            "name": "06_zero_then_cell",
            "parent": "03_zero_only",
            "releases": ["cell", "zero"],
        },
    ]
    if profile_mode != "locked":
        plan.append(
            {
                "name": f"07_profile_{profile_mode}",
                "parent": profile_base,
                "releases": ["cell", "zero", profile_mode],
                "classification": (
                    "exploratory" if profile_mode == "uvw" else "candidate"
                ),
            }
        )
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--xrd", required=True)
    parser.add_argument("--cif", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument(
        "--gsasii-path",
        help="GSAS-II source tree; defaults to GSASII_DIR or common locations",
    )
    parser.add_argument("--staging-root", default=str(DEFAULT_STAGING_ROOT))
    parser.add_argument(
        "--run-id",
        help="Stable run directory suffix; defaults to a UTC timestamp",
    )
    parser.add_argument("--xrd-format")
    parser.add_argument("--cif-format", default="CIF")
    parser.add_argument("--phase-name")
    parser.add_argument("--background-order", type=int, default=6)
    parser.add_argument("--max-refinement-passes", type=int, default=4)
    parser.add_argument("--max-shift-over-su", type=float, default=0.01)
    parser.add_argument("--two-theta-min", type=float)
    parser.add_argument("--two-theta-max", type=float)
    parser.add_argument(
        "--instrument-profile-status",
        choices=("calibrated", "uncalibrated"),
        required=True,
        help="Declare whether U/V/W come from a standard measured on this setup",
    )
    parser.add_argument(
        "--profile-mode",
        choices=("locked", "w", "uvw"),
        default="locked",
        help="Profile release after geometry sensitivity; uvw is exploratory only",
    )
    parser.add_argument(
        "--profile-base",
        choices=(
            "04_cell_zero_simultaneous",
            "05_cell_then_zero",
            "06_zero_then_cell",
        ),
        default="04_cell_zero_simultaneous",
    )
    parser.add_argument(
        "--select-candidate",
        help="Optional candidate to copy into selected/ after all audits pass",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Validate inputs and print the deterministic plan without running GSAS-II",
    )
    args = parser.parse_args()

    if not 2 <= args.background_order <= 20:
        raise SystemExit("--background-order must be between 2 and 20")
    if not 1 <= args.max_refinement_passes <= 20:
        raise SystemExit("--max-refinement-passes must be between 1 and 20")
    if args.max_shift_over_su <= 0:
        raise SystemExit("--max-shift-over-su must be positive")
    if (args.two_theta_min is None) != (args.two_theta_max is None):
        raise SystemExit("Specify both --two-theta-min and --two-theta-max")
    limits = None
    if args.two_theta_min is not None:
        if args.two_theta_min >= args.two_theta_max:
            raise SystemExit("two-theta minimum must be less than maximum")
        limits = (args.two_theta_min, args.two_theta_max)
    if args.instrument_profile_status == "uncalibrated" and args.profile_mode == "uvw":
        raise SystemExit(
            "Refusing free U/V/W with an uncalibrated instrument profile. "
            "Use locked or W-only, or supply calibrated instrument parameters."
        )

    xrd = require_file(args.xrd, "XRD")
    cif = require_file(args.cif, "CIF")
    instrument = require_file(args.instrument, "instrument parameter file")
    plan = build_plan(args.profile_mode, args.profile_base)
    plan_names = {item["name"] for item in plan}
    if args.select_candidate and args.select_candidate not in plan_names:
        raise SystemExit(
            f"--select-candidate must be one of: {', '.join(sorted(plan_names))}"
        )

    source_inputs = {
        "xrd": {"path": str(xrd), "bytes": xrd.stat().st_size, "sha256": sha256(xrd)},
        "cif": {"path": str(cif), "bytes": cif.stat().st_size, "sha256": sha256(cif)},
        "instrument": {
            "path": str(instrument),
            "bytes": instrument.stat().st_size,
            "sha256": sha256(instrument),
            "profile_status": args.instrument_profile_status,
        },
    }
    plan_payload = {
        "schema_version": 1,
        "sample_id": args.sample_id,
        "inputs": source_inputs,
        "settings": {
            "background_order": args.background_order,
            "limits": limits,
            "profile_mode": args.profile_mode,
            "profile_base": args.profile_base,
            "instrument_profile_status": args.instrument_profile_status,
            "max_refinement_passes": args.max_refinement_passes,
            "max_shift_over_su": args.max_shift_over_su,
        },
        "plan": plan,
    }
    if args.plan_only:
        print(json.dumps(plan_payload, ensure_ascii=False, indent=2))
        return 0

    gsasii_path = resolve_gsasii_path(args.gsasii_path)
    run_id = clean_name(
        args.run_id
        or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    staging_root = Path(args.staging_root).expanduser().resolve()
    run_dir = staging_root / f"{clean_name(args.sample_id)}_{run_id}"
    if run_dir.exists():
        raise SystemExit(f"Run staging directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    candidates_dir = run_dir / "candidates"
    candidates_dir.mkdir()
    summary_path = run_dir / "candidate_summary.json"
    summary: dict[str, Any] = {
        **plan_payload,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_dir": str(run_dir),
        "candidates": [],
        "selected_candidate": None,
    }
    write_json_atomic(summary_path, summary)

    if str(gsasii_path) not in sys.path:
        sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIscriptable as G2sc  # type: ignore

    initial_path = candidates_dir / "00_unrefined.gpx"
    project = G2sc.G2Project(newgpx=str(initial_path))
    histogram_kwargs = {}
    if args.xrd_format:
        histogram_kwargs["fmthint"] = args.xrd_format
    histogram = project.add_powder_histogram(
        str(xrd), str(instrument), **histogram_kwargs
    )
    phase = project.add_phase(
        str(cif),
        phasename=args.phase_name or clean_name(args.sample_id),
        histograms=[histogram],
        fmthint=args.cif_format,
    )
    configure_baseline(
        project,
        histogram,
        phase,
        background_order=args.background_order,
        limits=limits,
    )
    project.save(str(initial_path))

    outputs: dict[str, Path] = {"00_unrefined": initial_path}

    def run_candidate(
        spec: dict[str, Any],
        configure: Callable[[Any, Any], None],
    ) -> None:
        name = spec["name"]
        parent = spec["parent"]
        parent_path = outputs[parent]
        output_path = candidates_dir / f"{name}.gpx"
        copy_project(parent_path, output_path)
        try:
            candidate_project = G2sc.G2Project(str(output_path))
            candidate_histogram = candidate_project.histograms()[0]
            candidate_phase = candidate_project.phases()[0]
            configure(candidate_histogram, candidate_phase)
            candidate_project.set_Controls("cycles", 8)
            passes = 0
            while passes < args.max_refinement_passes:
                candidate_project.refine()
                candidate_project.save(str(output_path))
                passes += 1
                rvals = candidate_project["Covariance"]["data"].get("Rvals", {})
                max_shift = rvals.get("Max shft/sig")
                if (
                    rvals.get("converged", False)
                    and max_shift is not None
                    and float(max_shift) <= args.max_shift_over_su
                ):
                    break
            outputs[name] = output_path
            audit = collect_candidate(
                output_path,
                name=name,
                parent=parent,
                releases=spec["releases"],
                gsasii_path=gsasii_path,
            )
            audit["refinement_passes"] = passes
            audit["classification"] = spec.get("classification", "candidate")
        except Exception as exc:
            audit = {
                "name": name,
                "parent": parent,
                "releases": spec["releases"],
                "classification": spec.get("classification", "candidate"),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "gpx": {"path": str(output_path)},
            }
            if name == "01_scale_background":
                summary["candidates"].append(audit)
                write_json_atomic(summary_path, summary)
                raise
        summary["candidates"].append(audit)
        write_json_atomic(summary_path, summary)

    for spec in plan:
        if spec["parent"] not in outputs:
            summary["candidates"].append(
                {
                    "name": spec["name"],
                    "parent": spec["parent"],
                    "releases": spec["releases"],
                    "classification": spec.get("classification", "candidate"),
                    "status": "skipped",
                    "error": "Parent candidate did not succeed",
                }
            )
            write_json_atomic(summary_path, summary)
            continue
        if spec["name"] == "01_scale_background":
            run_candidate(
                spec,
                lambda hist, ph: configure_flags(
                    hist,
                    ph,
                    refine_cell=False,
                    refine_zero=False,
                    profile_mode="locked",
                ),
            )
        elif spec["name"] == "02_cell_only":
            run_candidate(
                spec,
                lambda hist, ph: configure_flags(
                    hist,
                    ph,
                    refine_cell=True,
                    refine_zero=False,
                    profile_mode="locked",
                ),
            )
        elif spec["name"] == "03_zero_only":
            run_candidate(
                spec,
                lambda hist, ph: configure_flags(
                    hist,
                    ph,
                    refine_cell=False,
                    refine_zero=True,
                    profile_mode="locked",
                ),
            )
        elif spec["name"] in {
            "04_cell_zero_simultaneous",
            "05_cell_then_zero",
            "06_zero_then_cell",
        }:
            run_candidate(
                spec,
                lambda hist, ph: configure_flags(
                    hist,
                    ph,
                    refine_cell=True,
                    refine_zero=True,
                    profile_mode="locked",
                ),
            )
        elif spec["name"].startswith("07_profile_"):
            run_candidate(
                spec,
                lambda hist, ph: configure_flags(
                    hist,
                    ph,
                    refine_cell=True,
                    refine_zero=True,
                    profile_mode=args.profile_mode,
                ),
            )

    if args.select_candidate:
        selected = next(
            item
            for item in summary["candidates"]
            if item["name"] == args.select_candidate
        )
        if selected["status"] != "succeeded":
            raise SystemExit("Selected candidate failed and cannot be finalized")
        severe = candidate_safety_errors(selected)
        if severe:
            raise SystemExit(
                "Selected candidate failed the automatic safety gate: "
                + "; ".join(severe)
            )
        selected_dir = run_dir / "selected"
        selected_dir.mkdir()
        selected_gpx = selected_dir / f"{clean_name(args.sample_id)}_refinement.gpx"
        shutil.copy2(outputs[args.select_candidate], selected_gpx)
        source_lst = outputs[args.select_candidate].with_suffix(".lst")
        if source_lst.is_file():
            shutil.copy2(
                source_lst,
                selected_dir / f"{clean_name(args.sample_id)}_refinement.lst",
            )
        selected_project = G2sc.G2Project(str(selected_gpx))
        selected_project.phases()[0].export_CIF(
            str(selected_dir / f"{clean_name(args.sample_id)}_result.cif")
        )
        summary["selected_candidate"] = args.select_candidate
        selected_paths = {
            "gpx": selected_gpx,
            "lst": selected_dir / f"{clean_name(args.sample_id)}_refinement.lst",
            "result_cif": selected_dir / f"{clean_name(args.sample_id)}_result.cif",
        }
        summary["selected_files"] = {
            role: {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for role, path in selected_paths.items()
        }
        write_json_atomic(summary_path, summary)

    print(run_dir)
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
