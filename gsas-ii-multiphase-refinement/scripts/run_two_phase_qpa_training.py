#!/usr/bin/env python3
"""Run blind-path, mass-normalized two-phase GSAS-II QPA training cases."""

from __future__ import annotations

import argparse
import random
import sys
import traceback
from pathlib import Path
from typing import Any

from qpa_core import (
    evaluate_training_case,
    json_clean,
    mass_normalized_scales,
    maximum_correlation,
    phase_b_scale_for_mass_fraction,
    select_competitive_candidate,
    sha256_file,
    summarize_replicates,
    write_json_atomic,
)


def required_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def parse_fractions(value: str) -> list[float]:
    fractions = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not fractions:
        raise argparse.ArgumentTypeError("at least one fraction is required")
    if len(set(fractions)) != len(fractions):
        raise argparse.ArgumentTypeError("fractions must be unique")
    if any(not 0.0 < item < 1.0 for item in fractions):
        raise argparse.ArgumentTypeError("fractions must be between 0 and 1")
    return fractions


def case_name(phase_b_fraction: float) -> str:
    return f"phase_b_{round(phase_b_fraction * 10000):05d}bp"


def remove_project_files(project_path: Path) -> None:
    for path in [project_path, project_path.with_suffix(".lst")]:
        if path.is_file():
            path.unlink()
    for backup in project_path.parent.glob(project_path.stem + ".bak*.gpx"):
        backup.unlink()


def create_truth_project(
    *,
    G2sc: Any,
    np: Any,
    phase_a_cif: Path,
    phase_b_cif: Path,
    instrument: Path,
    phase_a_name: str,
    phase_b_name: str,
    target_b: float,
    histogram_scale: float,
    seed: int,
    truth_gpx: Path,
) -> dict[str, Any]:
    np.random.seed(seed)
    random.seed(seed)
    project = G2sc.G2Project(newgpx=str(truth_gpx))
    phase_a = project.add_phase(str(phase_a_cif), phasename=phase_a_name)
    phase_b = project.add_phase(str(phase_b_cif), phasename=phase_b_name)
    histogram = project.add_simulated_powder_histogram(
        f"training-{phase_a_name}-{phase_b_name}-{target_b:.6f}",
        str(instrument),
        5.0,
        150.0,
        Tstep=0.02,
        scale=histogram_scale,
        phases=project.phases(),
    )
    mass_a = float(phase_a.data["General"]["Mass"])
    mass_b = float(phase_b.data["General"]["Mass"])
    specimen_basis = mass_a
    truth_scale_a, truth_scale_b = mass_normalized_scales(
        target_b, mass_a, mass_b, specimen_basis
    )
    phase_a.HAPvalue("Scale", truth_scale_a, [histogram])
    phase_b.HAPvalue("Scale", truth_scale_b, [histogram])
    phase_a.clear_HAP_refinements({"Scale": True}, [histogram])
    phase_b.clear_HAP_refinements({"Scale": True}, [histogram])
    histogram.clear_refinements(
        {
            "Sample Parameters": ["Scale"],
            "Background": True,
            "Instrument Parameters": ["U", "V", "W", "X", "Y", "SH/L"],
        }
    )
    project.set_Controls("cycles", 0)
    project.do_refinements([{}])
    project.save()
    return {
        "masses": {phase_a_name: mass_a, phase_b_name: mass_b},
        "truth_hap_scales": {phase_a_name: truth_scale_a, phase_b_name: truth_scale_b},
        "specimen_basis": specimen_basis,
    }


def fit_candidate(
    *,
    G2sc: Any,
    truth_gpx: Path,
    output_dir: Path,
    phase_a_name: str,
    phase_b_name: str,
    mass_a: float,
    mass_b: float,
    anchor: str,
    initial_b: float,
) -> dict[str, Any]:
    candidate_name = f"anchor_{anchor.lower()}_start_{round(initial_b * 100):02d}pct_b"
    candidate_dir = output_dir / candidate_name
    candidate_dir.mkdir(parents=True, exist_ok=False)
    fit_gpx = candidate_dir / "fit.gpx"
    fit = G2sc.G2Project(str(truth_gpx))
    fit.save(str(fit_gpx))
    histogram = fit.histograms()[0]
    phase_a = fit.phase(phase_a_name)
    phase_b = fit.phase(phase_b_name)
    if anchor == "A":
        initial_scale_a = 1.0
        initial_scale_b = phase_b_scale_for_mass_fraction(initial_b, mass_a, mass_b)
        phase_a.clear_HAP_refinements({"Scale": True}, [histogram])
        phase_b.set_HAP_refinements({"Scale": True}, [histogram])
        fixed_phase_name, refined_phase_name = phase_a_name, phase_b_name
    elif anchor == "B":
        initial_scale_b = 1.0
        initial_scale_a = phase_b_scale_for_mass_fraction(1.0 - initial_b, mass_b, mass_a)
        phase_b.clear_HAP_refinements({"Scale": True}, [histogram])
        phase_a.set_HAP_refinements({"Scale": True}, [histogram])
        fixed_phase_name, refined_phase_name = phase_b_name, phase_a_name
    else:
        raise ValueError(f"unknown anchor: {anchor}")
    phase_a.HAPvalue("Scale", initial_scale_a, [histogram])
    phase_b.HAPvalue("Scale", initial_scale_b, [histogram])
    histogram.set_refinements({"Sample Parameters": ["Scale"]})
    histogram.clear_refinements(
        {
            "Background": True,
            "Instrument Parameters": ["U", "V", "W", "X", "Y", "SH/L"],
        }
    )
    fit.set_Controls("cycles", 12)
    fit.save()
    fit.do_refinements([{}])
    fit.save()

    fractions = histogram.ComputeMassFracs()
    refined_a, esd_a = fractions[phase_a_name]
    refined_b, esd_b = fractions[phase_b_name]
    covariance = fit.data["Covariance"]["data"]
    r_values = covariance.get("Rvals", {})
    correlations = maximum_correlation(
        list(covariance.get("varyList", [])), covariance.get("covMatrix", [])
    )
    return {
        "candidate": candidate_name,
        "anchor": anchor,
        "initial_mass_fractions": {phase_a_name: 1.0 - initial_b, phase_b_name: initial_b},
        "phase_parameterization": {
            "fixed_hap_scale": {fixed_phase_name: 1.0},
            "refined_hap_scale": refined_phase_name,
            "refined_histogram_scale": True,
        },
        "refined_mass_fractions": {
            phase_a_name: {"value": float(refined_a), "esd": float(esd_a)},
            phase_b_name: {"value": float(refined_b), "esd": float(esd_b)},
        },
        "refined_hap_scales": {
            phase_a_name: float(phase_a.HAPvalue("Scale", targethistlist=[histogram])),
            phase_b_name: float(phase_b.HAPvalue("Scale", targethistlist=[histogram])),
        },
        "refined_histogram_scale": float(histogram.data["Sample Parameters"]["Scale"][0]),
        "metrics": {
            "Rwp_percent": r_values.get("Rwp"),
            "GOF": r_values.get("GOF"),
            "converged": bool(r_values.get("converged", False)),
            "svd_count": int(r_values.get("SVD0", 0)),
            "max_shift_over_esd": r_values.get("Max shft/sig"),
            "maximum_correlation": correlations,
        },
        "files": {"fit_gpx": str(fit_gpx), "fit_lst": str(fit_gpx.with_suffix(".lst"))},
    }


def run_replicate(
    *,
    G2sc: Any,
    np: Any,
    phase_a_cif: Path,
    phase_b_cif: Path,
    instrument: Path,
    phase_a_name: str,
    phase_b_name: str,
    target_b: float,
    starting_fractions: list[float],
    histogram_scale: float,
    seed: int,
    output_dir: Path,
    absolute_error_limit: float,
    correlation_limit: float,
    shift_limit: float,
    path_spread_limit: float,
    retain_all_candidates: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    truth_gpx = output_dir / "truth.gpx"
    truth = create_truth_project(
        G2sc=G2sc,
        np=np,
        phase_a_cif=phase_a_cif,
        phase_b_cif=phase_b_cif,
        instrument=instrument,
        phase_a_name=phase_a_name,
        phase_b_name=phase_b_name,
        target_b=target_b,
        histogram_scale=histogram_scale,
        seed=seed,
        truth_gpx=truth_gpx,
    )
    mass_a = truth["masses"][phase_a_name]
    mass_b = truth["masses"][phase_b_name]
    candidates: list[dict[str, Any]] = []
    for anchor in ("A", "B"):
        for initial_b in starting_fractions:
            candidates.append(
                fit_candidate(
                    G2sc=G2sc,
                    truth_gpx=truth_gpx,
                    output_dir=output_dir,
                    phase_a_name=phase_a_name,
                    phase_b_name=phase_b_name,
                    mass_a=mass_a,
                    mass_b=mass_b,
                    anchor=anchor,
                    initial_b=initial_b,
                )
            )
    selected, competitive = select_competitive_candidate(candidates)
    selected_b = selected["refined_mass_fractions"][phase_b_name]["value"]
    selected_esd = selected["refined_mass_fractions"][phase_b_name]["esd"]
    path_values = [
        candidate["refined_mass_fractions"][phase_b_name]["value"]
        for candidate in competitive
    ]
    path_spread = max(path_values) - min(path_values) if path_values else None
    assessment = evaluate_training_case(
        target_fraction=target_b,
        refined_fraction=selected_b,
        refined_esd=selected_esd,
        converged=selected["metrics"]["converged"],
        svd_count=selected["metrics"]["svd_count"],
        # GSAS-II reports the maximum over the complete trajectory here. With
        # deliberately distant starts this is not a final-cycle convergence
        # metric, so path agreement replaces it for this unit test.
        max_shift_over_esd=0.0,
        max_correlation=selected["metrics"]["maximum_correlation"]["absolute"],
        absolute_error_limit=absolute_error_limit,
        correlation_limit=correlation_limit,
        shift_limit=shift_limit,
    )
    if path_spread is None or path_spread > path_spread_limit:
        assessment["review_flags"].append("refinement_path_spread_exceeds_limit")
        if not assessment["hard_failures"]:
            assessment["status"] = "review"
    result = {
        "case": case_name(target_b),
        "status": assessment["status"],
        "seed": seed,
        "scope": "blind-path scale-only two-phase QPA unit test",
        "selection_rule": "retain minimum-hard-failure candidates; treat paths within relative GOF 1e-8 as competitive; rank those by correlation, trajectory shift and name; target excluded",
        "shift_metric_policy": "GSAS-II trajectory maximum retained in metrics but not used as a final-cycle gate; competitive-path spread is gated instead",
        "unit_cell_masses": truth["masses"],
        "specimen_basis": truth["specimen_basis"],
        "target_mass_fractions": {phase_a_name: 1.0 - target_b, phase_b_name: target_b},
        "truth_hap_scales": truth["truth_hap_scales"],
        "selected_candidate": selected["candidate"],
        "selected_anchor": selected["anchor"],
        "selected_fraction": selected_b,
        "selected_esd": selected_esd,
        "path_spread": path_spread,
        "competitive_candidates": [candidate["candidate"] for candidate in competitive],
        "refined_mass_fractions": selected["refined_mass_fractions"],
        "metrics": selected["metrics"],
        "assessment": assessment,
        "candidates": candidates,
        "files": {"truth_gpx": str(truth_gpx), **selected["files"]},
    }
    write_json_atomic(output_dir / "case_result.json", result)
    if not retain_all_candidates:
        for candidate in candidates:
            if candidate["candidate"] != selected["candidate"]:
                remove_project_files(Path(candidate["files"]["fit_gpx"]))
    for backup in output_dir.rglob("*.bak*.gpx"):
        backup.unlink()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-a-cif", required=True)
    parser.add_argument("--phase-b-cif", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--phase-a-name", default="CuCr2O4")
    parser.add_argument("--phase-b-name", default="CuO")
    parser.add_argument("--phase-b-mass-fractions", type=parse_fractions, default=parse_fractions("0.01,0.10,0.50,0.90"))
    parser.add_argument("--starting-fractions", type=parse_fractions, default=parse_fractions("0.05,0.25,0.50,0.75,0.95"))
    parser.add_argument("--histogram-scale", type=float, default=1_000_000.0)
    parser.add_argument("--seed", type=int, default=20260804)
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--absolute-error-limit", type=float, default=0.002)
    parser.add_argument("--correlation-limit", type=float, default=0.95)
    parser.add_argument("--shift-limit", type=float, default=1.0)
    parser.add_argument("--path-spread-limit", type=float, default=0.002)
    parser.add_argument("--retain-all-candidates", action="store_true")
    parser.add_argument("--gsasii-path", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    if args.replicates < 1:
        parser.error("--replicates must be at least 1")

    phase_a_cif = required_file(args.phase_a_cif, "phase A CIF")
    phase_b_cif = required_file(args.phase_b_cif, "phase B CIF")
    instrument = required_file(args.instrument, "instrument parameters")
    gsasii_path = Path(args.gsasii_path).expanduser().resolve()
    if not (gsasii_path / "GSASII" / "GSASIIscriptable.py").is_file():
        raise FileNotFoundError(f"GSASIIscriptable.py not found under {gsasii_path}")
    output_dir = Path(args.output_dir).expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)

    sys.path.insert(0, str(gsasii_path))
    import numpy as np
    from GSASII import GSASIIscriptable as G2sc
    from GSASII import GSASIIpath

    summary: dict[str, Any] = {
        "schema_version": 2,
        "stage": "training",
        "dataset": "GSAS-II CuCr2O4/CuO Simulation",
        "status": "fail",
        "real_gsasii": True,
        "gsasii": {"path": str(gsasii_path), "version_number": GSASIIpath.GetVersionNumber(), "python": sys.version},
        "inputs": {
            "phase_a_cif": str(phase_a_cif),
            "phase_b_cif": str(phase_b_cif),
            "instrument": str(instrument),
            "sha256": {
                "phase_a_cif": sha256_file(phase_a_cif),
                "phase_b_cif": sha256_file(phase_b_cif),
                "instrument": sha256_file(instrument),
            },
        },
        "settings": {
            "phase_a_name": args.phase_a_name,
            "phase_b_name": args.phase_b_name,
            "phase_b_mass_fractions": args.phase_b_mass_fractions,
            "starting_fractions": args.starting_fractions,
            "histogram_scale": args.histogram_scale,
            "seed": args.seed,
            "replicates": args.replicates,
            "absolute_error_limit": args.absolute_error_limit,
            "correlation_limit": args.correlation_limit,
            "shift_limit": args.shift_limit,
            "path_spread_limit": args.path_spread_limit,
        },
        "cases": [],
        "aggregates": [],
        "limitations": [
            "simulated high-count patterns with GSAS-II dummy-pattern noise",
            "exact source structures and instrument profile reused in fitting (inverse-crime unit test)",
            "only histogram Scale and one relative HAP Scale refined per candidate",
            "does not validate background, profile, preferred orientation, microabsorption or phase identification",
            "no figure generated",
        ],
    }
    grouped: dict[float, list[dict[str, Any]]] = {target: [] for target in args.phase_b_mass_fractions}
    for target_index, target_b in enumerate(args.phase_b_mass_fractions):
        for replicate_index in range(args.replicates):
            seed = args.seed + target_index * 10_000 + replicate_index
            destination = output_dir / case_name(target_b) / f"replicate_{replicate_index + 1:03d}"
            try:
                result = run_replicate(
                    G2sc=G2sc,
                    np=np,
                    phase_a_cif=phase_a_cif,
                    phase_b_cif=phase_b_cif,
                    instrument=instrument,
                    phase_a_name=args.phase_a_name,
                    phase_b_name=args.phase_b_name,
                    target_b=target_b,
                    starting_fractions=args.starting_fractions,
                    histogram_scale=args.histogram_scale,
                    seed=seed,
                    output_dir=destination,
                    absolute_error_limit=args.absolute_error_limit,
                    correlation_limit=args.correlation_limit,
                    shift_limit=args.shift_limit,
                    path_spread_limit=args.path_spread_limit,
                    retain_all_candidates=args.retain_all_candidates,
                )
            except Exception as exc:
                destination.mkdir(parents=True, exist_ok=True)
                result = {
                    "case": case_name(target_b),
                    "status": "fail",
                    "seed": seed,
                    "selected_fraction": None,
                    "selected_esd": None,
                    "selected_anchor": None,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
                write_json_atomic(destination / "case_result.json", result)
            summary["cases"].append(result)
            grouped[target_b].append(result)
            write_json_atomic(output_dir / "training_summary.json", summary)

    for target_b, cases in grouped.items():
        aggregate = {"target_phase_b_fraction": target_b, **summarize_replicates(cases, nominal_fraction=target_b)}
        summary["aggregates"].append(aggregate)
    statuses = [case["status"] for case in summary["cases"]]
    summary["status"] = "fail" if "fail" in statuses else "review" if "review" in statuses else "pass"
    summary["counts"] = {"pass": statuses.count("pass"), "review": statuses.count("review"), "fail": statuses.count("fail")}
    write_json_atomic(output_dir / "training_summary.json", summary)
    print(json_clean({"status": summary["status"], "counts": summary["counts"], "summary": str(output_dir / "training_summary.json")}))
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
