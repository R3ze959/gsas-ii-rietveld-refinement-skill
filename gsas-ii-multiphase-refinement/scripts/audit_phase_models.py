#!/usr/bin/env python3
"""Audit CIF composition and GSAS-II import integrity before multiphase QPA."""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from qpa_core import (
    assess_phase_model_import,
    compare_phase_compositions,
    element_symbol,
    json_clean,
    sha256_file,
    write_json_atomic,
)


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


def parse_named_files(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"phase must use NAME=/path syntax: {value}")
        name, raw_path = (item.strip() for item in value.split("=", 1))
        if not name or name in parsed:
            raise ValueError(f"blank or duplicate phase name: {name!r}")
        parsed[name] = required_file(raw_path, f"phase {name}")
    return parsed


def cif_number(value: Any) -> float:
    text = str(value).strip()
    match = re.match(
        r"^[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+\-]?\d+)?",
        text,
    )
    if not match:
        raise ValueError(f"not a CIF number: {value!r}")
    return float(match.group(0))


def cif_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def parse_formula(value: str) -> dict[str, float]:
    compact = re.sub(r"\s+", "", str(value))
    matches = list(re.finditer(r"([A-Z][a-z]?)(\d*\.?\d*)", compact))
    if not matches or "".join(match.group(0) for match in matches) != compact:
        raise ValueError(f"unsupported CIF formula syntax: {value!r}")
    composition: dict[str, float] = {}
    for match in matches:
        symbol = match.group(1)
        amount = float(match.group(2)) if match.group(2) else 1.0
        composition[symbol] = composition.get(symbol, 0.0) + amount
    return composition


def symmetry_expression_value(expression: str, coordinates: dict[str, float]) -> float:
    """Evaluate one CIF xyz expression with a deliberately small safe grammar."""
    node = ast.parse(expression.strip(), mode="eval")

    def evaluate(item: ast.AST) -> float:
        if isinstance(item, ast.Expression):
            return evaluate(item.body)
        if isinstance(item, ast.Name) and item.id.lower() in coordinates:
            return float(coordinates[item.id.lower()])
        if isinstance(item, ast.Constant) and isinstance(item.value, (int, float)):
            return float(item.value)
        if isinstance(item, ast.UnaryOp) and isinstance(item.op, (ast.UAdd, ast.USub)):
            value = evaluate(item.operand)
            return value if isinstance(item.op, ast.UAdd) else -value
        if isinstance(item, ast.BinOp) and isinstance(
            item.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left, right = evaluate(item.left), evaluate(item.right)
            if isinstance(item.op, ast.Add):
                return left + right
            if isinstance(item.op, ast.Sub):
                return left - right
            if isinstance(item.op, ast.Mult):
                return left * right
            return left / right
        raise ValueError(f"unsupported CIF symmetry expression: {expression!r}")

    return evaluate(node)


def multiplicity_from_symmetry_operations(
    operations: list[Any], coordinates: list[float]
) -> int:
    """Count the unique periodic orbit from an explicit CIF symmetry loop."""
    variables = dict(zip(("x", "y", "z"), coordinates))
    positions: list[tuple[float, float, float]] = []
    for operation in operations:
        expressions = [item.strip() for item in str(operation).split(",")]
        if len(expressions) != 3:
            raise ValueError(f"invalid CIF symmetry operation: {operation!r}")
        position = tuple(
            symmetry_expression_value(expression, variables) % 1.0
            for expression in expressions
        )
        duplicate = any(
            all(
                min(abs(a - b), 1.0 - abs(a - b)) <= 2e-4
                for a, b in zip(position, existing)
            )
            for existing in positions
        )
        if not duplicate:
            positions.append(position)
    if not positions:
        raise ValueError("CIF explicit symmetry-operation loop is empty")
    return len(positions)


def source_cell_composition(
    block: Any,
    *,
    site_multiplicity: Callable[[str, list[float]], int] | None = None,
) -> tuple[dict[str, float], list[str], dict[str, Any]]:
    """Read unit-cell composition independently from the GSAS-II phase import."""
    types = block.get("_atom_site_type_symbol")
    multiplicities = block.get("_atom_site_symmetry_multiplicity")
    occupancies = block.get("_atom_site_occupancy")
    occupancy_issues: list[str] = []
    metadata: dict[str, Any] = {}
    if types is not None and multiplicities is None:
        x_values = cif_list(block.get("_atom_site_fract_x"))
        y_values = cif_list(block.get("_atom_site_fract_y"))
        z_values = cif_list(block.get("_atom_site_fract_z"))
        type_values = cif_list(types)
        if not (len(type_values) == len(x_values) == len(y_values) == len(z_values)):
            raise ValueError("CIF atom-site type and fractional-coordinate lengths differ")
        space_group = None
        for key in (
            "_space_group_name_H-M_alt",
            "_symmetry_space_group_name_H-M",
            "_space_group_name_Hall",
            "_symmetry_space_group_name_Hall",
        ):
            value = block.get(key)
            if value is not None:
                space_group = str(value).strip()
                break
        if not space_group:
            for key in (
                "_space_group_IT_number",
                "_symmetry_Int_Tables_number",
            ):
                value = block.get(key)
                if value is not None:
                    space_group = f"number:{int(cif_number(value))}"
                    break
        if space_group and site_multiplicity is not None:
            try:
                multiplicities = [
                    site_multiplicity(
                        space_group,
                        [cif_number(x), cif_number(y), cif_number(z)],
                    )
                    for x, y, z in zip(x_values, y_values, z_values)
                ]
                metadata["multiplicity_source"] = (
                    "coordinate_orbit_from_declared_space_group"
                )
            except Exception as exc:
                metadata["declared_space_group_orbit_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                multiplicities = None
        if multiplicities is None:
            operations = None
            for key in (
                "_space_group_symop_operation_xyz",
                "_symmetry_equiv_pos_as_xyz",
            ):
                value = block.get(key)
                if value is not None:
                    operations = cif_list(value)
                    break
            if operations is None:
                raise ValueError(
                    "CIF lacks site multiplicity, a usable declared space group, "
                    "and an explicit symmetry-operation loop"
                )
            multiplicities = [
                multiplicity_from_symmetry_operations(
                    operations,
                    [cif_number(x), cif_number(y), cif_number(z)],
                )
                for x, y, z in zip(x_values, y_values, z_values)
            ]
            metadata["multiplicity_source"] = (
                "coordinate_orbit_from_explicit_symmetry_operations"
            )

    if types is not None and multiplicities is not None:
        type_values = cif_list(types)
        multiplicity_values = cif_list(multiplicities)
        occupancy_values = (
            cif_list(occupancies) if occupancies is not None else [1.0] * len(type_values)
        )
        if not (
            len(type_values) == len(multiplicity_values) == len(occupancy_values)
        ):
            raise ValueError("CIF atom-site type, multiplicity and occupancy lengths differ")
        composition: dict[str, float] = {}
        for index, (raw_type, raw_multiplicity, raw_occupancy) in enumerate(
            zip(type_values, multiplicity_values, occupancy_values), start=1
        ):
            symbol = element_symbol(str(raw_type))
            multiplicity = cif_number(raw_multiplicity)
            occupancy = cif_number(raw_occupancy)
            if multiplicity <= 0.0:
                occupancy_issues.append(f"site_{index}_nonpositive_multiplicity")
            if not 0.0 < occupancy <= 1.0:
                occupancy_issues.append(f"site_{index}_occupancy={occupancy}")
            composition[symbol] = composition.get(symbol, 0.0) + multiplicity * occupancy
        metadata["composition_source"] = (
            "atom_site_symmetry_multiplicity_times_occupancy"
        )
    else:
        formula = block.get("_chemical_formula_sum")
        z_value = block.get("_cell_formula_units_Z")
        if formula is None or z_value is None:
            raise ValueError(
                "CIF lacks atom-site multiplicity or chemical_formula_sum plus cell_formula_units_Z"
            )
        formula_composition = parse_formula(str(formula))
        z = cif_number(z_value)
        composition = {name: amount * z for name, amount in formula_composition.items()}
        metadata["composition_source"] = "chemical_formula_sum_times_Z"

    formula = block.get("_chemical_formula_sum")
    z_value = block.get("_cell_formula_units_Z")
    if formula is not None and z_value is not None:
        try:
            formula_composition = parse_formula(str(formula))
            z = cif_number(z_value)
            formula_cell = {name: amount * z for name, amount in formula_composition.items()}
            metadata["formula_z_comparison"] = compare_phase_compositions(
                composition, formula_cell
            )
        except ValueError as exc:
            metadata["formula_z_comparison"] = {
                "status": "review",
                "error": str(exc),
            }
    elif formula is not None:
        try:
            formula_composition = parse_formula(str(formula))
            if set(formula_composition) != set(composition):
                raise ValueError("formula and atom-site element sets differ")
            inferred_z_values = {
                name: float(composition[name]) / float(amount)
                for name, amount in formula_composition.items()
                if float(amount) > 0.0
            }
            inferred_z = sum(inferred_z_values.values()) / len(inferred_z_values)
            consistent = all(
                math.isclose(value, inferred_z, rel_tol=0.005, abs_tol=0.05)
                for value in inferred_z_values.values()
            )
            metadata["formula_inferred_z_comparison"] = {
                "status": "pass" if consistent else "review",
                "inferred_z": inferred_z,
                "elementwise_z": inferred_z_values,
            }
        except ValueError as exc:
            metadata["formula_inferred_z_comparison"] = {
                "status": "review",
                "error": str(exc),
            }
    return composition, occupancy_issues, metadata


def warning_lines(text: str) -> str:
    retained = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(word in lowered for word in ("warning", "not compatible", "not matched", "error")):
            retained.append(line.strip())
    return "\n".join(line for line in retained if line)


def imported_composition(general: dict[str, Any]) -> dict[str, float]:
    result: dict[str, float] = {}
    for raw_type, raw_count in general.get("NoAtoms", {}).items():
        symbol = element_symbol(str(raw_type))
        result[symbol] = result.get(symbol, 0.0) + float(raw_count)
    return result


def audit_phase_models(
    *,
    G2sc: Any,
    G2elem: Any,
    G2spc: Any,
    phases: dict[str, Path],
    mass_relative_tolerance: float = 0.005,
) -> dict[str, Any]:
    import CifFile

    records: dict[str, Any] = {}
    for name, path in phases.items():
        try:
            cif = CifFile.ReadCif(str(path))
            block_names = list(cif.keys())
            if not block_names:
                raise ValueError("CIF contains no data block")
            block = cif[block_names[0]]
            def site_multiplicity(space_group: str, coordinates: list[float]) -> int:
                if space_group.startswith("number:"):
                    number = int(space_group.split(":", 1)[1])
                    if not 1 <= number < len(G2spc.spgbyNum):
                        raise ValueError(f"invalid International Tables number: {number}")
                    space_group = G2spc.spgbyNum[number]
                error, space_group_data = G2spc.SpcGroup(space_group)
                if error:
                    raise ValueError(
                        f"cannot generate symmetry orbit for {space_group!r}: {error}"
                    )
                multiplicity = len(
                    list(G2spc.GenAtom(coordinates, space_group_data))
                )
                if multiplicity <= 0:
                    raise ValueError("generated site multiplicity is nonpositive")
                return multiplicity

            source_composition, occupancy_issues, metadata = source_cell_composition(
                block, site_multiplicity=site_multiplicity
            )
            expected_mass = sum(
                amount * float(G2elem.GetAtomInfo(symbol)["Mass"])
                for symbol, amount in source_composition.items()
            )
            with tempfile.TemporaryDirectory(prefix="gsasii-phase-audit-") as temporary:
                gpx = Path(temporary) / "audit.gpx"
                capture = io.StringIO()
                with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
                    project = G2sc.G2Project(newgpx=str(gpx))
                    phase = project.add_phase(str(path), phasename=name)
                general = phase.data["General"]
            filtered_log = warning_lines(capture.getvalue())
            assessment = assess_phase_model_import(
                source_composition=source_composition,
                imported_composition=imported_composition(general),
                expected_cell_mass=expected_mass,
                imported_cell_mass=float(general["Mass"]),
                import_log=filtered_log,
                occupancy_issues=occupancy_issues,
                mass_relative_tolerance=mass_relative_tolerance,
            )
            formula_check = metadata.get(
                "formula_z_comparison",
                metadata.get("formula_inferred_z_comparison", {}),
            )
            metadata_flags = []
            if formula_check.get("status") not in {None, "pass"}:
                metadata_flags.append("formula_z_metadata_disagrees_with_atom_sites")
            if metadata.get("declared_space_group_orbit_error"):
                metadata_flags.append(
                    "declared_space_group_unusable_used_explicit_symmetry_operations"
                )
            record = {
                "phase": name,
                "source": {"path": str(path), "sha256": sha256_file(path)},
                "source_data_block": block_names[0],
                "source_unit_cell_composition": source_composition,
                "source_metadata": metadata,
                "imported": {
                    "space_group": general.get("SGData", {}).get("SpGrp"),
                    "cell": json_clean(general.get("Cell")),
                    "unit_cell_composition": imported_composition(general),
                    "unit_cell_mass": float(general["Mass"]),
                },
                "gsasii_import_warning_log": filtered_log,
                "assessment": assessment,
                "metadata_review_flags": metadata_flags,
                "status": (
                    "fail"
                    if assessment["hard_failures"]
                    else "review"
                    if assessment["review_flags"] or metadata_flags
                    else "pass"
                ),
            }
        except Exception as exc:
            record = {
                "phase": name,
                "source": {"path": str(path), "sha256": sha256_file(path)},
                "status": "fail",
                "assessment": {
                    "status": "fail",
                    "hard_failures": [f"phase_model_audit_error:{type(exc).__name__}:{exc}"],
                    "review_flags": [],
                },
            }
        records[name] = record
    hard_failures = [
        f"{name}:{failure}"
        for name, record in records.items()
        for failure in record.get("assessment", {}).get("hard_failures", [])
    ]
    review_flags = [
        f"{name}:{flag}"
        for name, record in records.items()
        for flag in (
            record.get("assessment", {}).get("review_flags", [])
            + record.get("metadata_review_flags", [])
        )
    ]
    return {
        "schema_version": 1,
        "status": "fail" if hard_failures else "review" if review_flags else "pass",
        "phase_count": len(records),
        "hard_failures": hard_failures,
        "review_flags": review_flags,
        "phases": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", action="append", required=True, help="NAME=/path/to/phase.cif")
    parser.add_argument("--output", required=True)
    parser.add_argument("--mass-relative-tolerance", type=float, default=0.005)
    parser.add_argument("--gsasii-path")
    args = parser.parse_args()
    if args.mass_relative_tolerance < 0.0:
        parser.error("--mass-relative-tolerance must be nonnegative")
    phases = parse_named_files(args.phase)
    gsasii_path = resolve_gsasii_path(args.gsasii_path)
    sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIElem as G2elem
    from GSASII import GSASIIscriptable as G2sc
    from GSASII import GSASIIspc as G2spc
    from GSASII import GSASIIpath

    G2sc.SetPrintLevel("warn")
    result = audit_phase_models(
        G2sc=G2sc,
        G2elem=G2elem,
        G2spc=G2spc,
        phases=phases,
        mass_relative_tolerance=args.mass_relative_tolerance,
    )
    result["gsasii"] = {
        "path": str(gsasii_path),
        "version_number": GSASIIpath.GetVersionNumber(),
        "python": sys.version,
    }
    output = Path(args.output).expanduser().resolve()
    write_json_atomic(output, result)
    print(json.dumps(json_clean({"status": result["status"], "output": str(output)}), indent=2))
    return 0 if result["status"] != "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
