#!/usr/bin/env python3
"""Score evidence-bound, independently refined CIF variants for one phase."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from qpa_common_problems import (
    select_constrained_model_variant,
    validate_model_grid_evidence,
)
from qpa_core import sha256_file, write_json_atomic


def required_file(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path


def parse_variants(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"variant must use LABEL=/path/to/qpa_summary.json: {value}")
        label, raw_path = (item.strip() for item in value.split("=", 1))
        if not label or label in parsed:
            raise ValueError(f"blank or duplicate variant label: {label!r}")
        parsed[label] = required_file(raw_path, f"variant {label}")
    if len(parsed) < 2:
        raise ValueError("a constrained model grid requires at least two variants")
    return parsed


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return data


def protocol_from_summary(summary: dict[str, Any], summary_path: Path) -> tuple[Path, dict[str, Any]]:
    raw_path = summary.get("protocol_manifest")
    if not raw_path:
        raise ValueError(f"summary does not name a protocol manifest: {summary_path}")
    protocol_path = Path(raw_path)
    if not protocol_path.is_absolute():
        protocol_path = (summary_path.parent / protocol_path).resolve()
    protocol_path = required_file(str(protocol_path), "protocol manifest")
    return protocol_path, load_json(protocol_path, "protocol manifest")


def validate_variant_summary(
    summary: dict[str, Any], *, label: str, target_phase: str
) -> None:
    """Reject incomplete files that could otherwise look like clean variants."""
    version = summary.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 4:
        raise ValueError(f"variant summary schema_version must be at least 4: {label}")
    if summary.get("status") not in {"pass", "review", "fail"}:
        raise ValueError(f"variant summary has an invalid status: {label}")
    assessment = summary.get("scientific_assessment")
    if not isinstance(assessment, dict):
        raise ValueError(f"variant scientific_assessment is missing: {label}")
    if assessment.get("status") != summary.get("status"):
        raise ValueError(f"variant summary and scientific statuses disagree: {label}")
    for key in ("hard_failures", "review_flags"):
        if not isinstance(assessment.get(key), list):
            raise ValueError(f"variant scientific_assessment.{key} is missing: {label}")
    phase_audit = summary.get("phase_model_audit")
    if not isinstance(phase_audit, dict) or phase_audit.get("status") not in {
        "pass",
        "review",
        "fail",
    }:
        raise ValueError(f"variant phase_model_audit is missing or invalid: {label}")
    if phase_audit.get("status") == "fail" and not assessment["hard_failures"]:
        raise ValueError(f"failed phase-model audit lacks a hard failure: {label}")
    selected = summary.get("selected_result")
    if not isinstance(selected, dict):
        raise ValueError(f"variant selected_result is missing: {label}")
    metrics = selected.get("metrics")
    gof = metrics.get("GOF") if isinstance(metrics, dict) else None
    try:
        valid_gof = (
            gof is not None
            and math.isfinite(float(gof))
            and float(gof) > 0.0
        )
    except (TypeError, ValueError):
        valid_gof = False
    if not valid_gof:
        raise ValueError(f"variant selected GOF is missing or invalid: {label}")
    fractions = selected.get("sample_normalized_mass_fractions")
    if not isinstance(fractions, dict) or target_phase not in fractions:
        raise ValueError(f"variant sample fractions omit target phase: {label}")


def invariant_signature(protocol: dict[str, Any], target_phase: str) -> dict[str, Any]:
    inputs = protocol.get("inputs", {})
    phases = inputs.get("phases", {})
    if target_phase not in phases:
        raise ValueError(f"target phase is absent from protocol: {target_phase}")
    other_phase_hashes = {
        name: record.get("sha256")
        for name, record in phases.items()
        if name != target_phase
    }
    return {
        "pattern_sha256": inputs.get("pattern", {}).get("sha256"),
        "instrument_sha256": inputs.get("instrument", {}).get("sha256"),
        "other_phase_hashes": other_phase_hashes,
        "phase_names": sorted(phases),
        "settings": protocol.get("settings"),
        "answer_status_at_freeze": protocol.get("answer_status_at_freeze"),
        "answer_values_present": protocol.get("answer_values_present"),
    }


def score_grid(
    *,
    target_phase: str,
    evidence_file: Path,
    variant_paths: dict[str, Path],
    relative_gof_tolerance: float,
) -> dict[str, Any]:
    variants = []
    signatures = []
    target_hashes = set()
    evidence_data = load_json(evidence_file, "model-grid evidence")
    evidence_contract = validate_model_grid_evidence(
        evidence_data,
        target_phase=target_phase,
        variant_labels=list(variant_paths),
    )
    for label, summary_path in variant_paths.items():
        summary = load_json(summary_path, f"variant summary {label}")
        if summary.get("real_gsasii") is not True:
            raise ValueError(f"variant is not marked as a real GSAS-II result: {label}")
        validate_variant_summary(summary, label=label, target_phase=target_phase)
        protocol_path, protocol = protocol_from_summary(summary, summary_path)
        protocol_version = protocol.get("schema_version")
        if (
            isinstance(protocol_version, bool)
            or not isinstance(protocol_version, int)
            or protocol_version < 4
        ):
            raise ValueError(f"variant protocol schema_version must be at least 4: {label}")
        signature = invariant_signature(protocol, target_phase)
        signatures.append(signature)
        phase_record = protocol["inputs"]["phases"][target_phase]
        target_hash = phase_record.get("sha256")
        if not target_hash:
            raise ValueError(f"target phase hash is missing for variant: {label}")
        target_hashes.add(target_hash)
        selected = summary.get("selected_result") or {}
        variants.append(
            {
                "label": label,
                "summary": {"path": str(summary_path), "sha256": sha256_file(summary_path)},
                "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
                "target_phase_model": phase_record,
                "status": summary.get("status"),
                "scientific_assessment": summary.get("scientific_assessment", {}),
                "metrics": selected.get("metrics", {}),
                "sample_normalized_mass_fractions": selected.get(
                    "sample_normalized_mass_fractions"
                ),
            }
        )
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise ValueError(
            "variant protocols differ outside the target-phase CIF; rerun the frozen grid"
        )
    if len(target_hashes) != len(variant_paths):
        raise ValueError("two variant labels point to the same target-phase CIF hash")
    selection = select_constrained_model_variant(
        variants, relative_gof_tolerance=relative_gof_tolerance
    )
    selected_label = selection["selected_label"]
    selected_variant = next(item for item in variants if item["label"] == selected_label)
    return {
        "schema_version": 1,
        "status": selection["status"],
        "target_phase": target_phase,
        "evidence": {
            "path": str(evidence_file),
            "sha256": sha256_file(evidence_file),
            "validated_contract": evidence_contract,
        },
        "relative_gof_tolerance": relative_gof_tolerance,
        "selection": selection,
        "selected_variant": selected_variant,
        "variants": variants,
        "limitations": [
            "all variants must be frozen CIFs refined under identical non-target settings",
            "a lower GOF does not prove dopant content or crystallographic site assignment",
            "a constrained XRD model grid can select a preferred profile model but remains review for dopant content or site claims",
            "indistinguishable variants remain review and require independent composition/site evidence",
            "no figure generated",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-phase", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument(
        "--variant",
        action="append",
        required=True,
        help="LABEL=/path/to/qpa_summary.json",
    )
    parser.add_argument("--relative-gof-tolerance", type=float, default=1e-5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.relative_gof_tolerance < 0.0:
        parser.error("--relative-gof-tolerance must be nonnegative")
    try:
        variants = parse_variants(args.variant)
        evidence = required_file(args.evidence_file, "model-grid evidence")
        result = score_grid(
            target_phase=args.target_phase,
            evidence_file=evidence,
            variant_paths=variants,
            relative_gof_tolerance=args.relative_gof_tolerance,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    write_json_atomic(output, result)
    print(json.dumps({"status": result["status"], "output": str(output)}, indent=2))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
