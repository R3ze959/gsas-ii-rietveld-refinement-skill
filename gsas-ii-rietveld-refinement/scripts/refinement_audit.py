#!/usr/bin/env python3
"""Shared GSAS-II result extraction and validation helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any


METRIC_LABELS = {
    "Rwp": "Rwp",
    "Rp": "Rp",
    "R_bkg": "R-bkg",
    "wR_bkg": "wR-bkg",
    "RF": "RF",
    "RF2": "RF²",
    "GOF": "GOF",
}


def default_data_root(environment_name: str, directory_name: str) -> Path:
    """Return an environment-configured root or a portable home-directory default."""
    configured = os.environ.get(environment_name)
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / directory_name).resolve()


def resolve_gsasii_path(value: str | None) -> Path:
    """Locate a GSAS-II source tree without embedding a machine-specific path."""
    candidates: list[Path] = []
    if value:
        candidates.append(Path(value).expanduser())
    configured = os.environ.get("GSASII_DIR")
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        [
            Path.home() / "g2main" / "GSAS-II",
            Path.home() / "GSAS-II",
        ]
    )
    checked: list[str] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if str(resolved) in checked:
            continue
        checked.append(str(resolved))
        if (resolved / "GSASII" / "GSASIIscriptable.py").is_file():
            return resolved
    locations = ", ".join(checked) if checked else "none"
    raise SystemExit(
        "GSASIIscriptable.py was not found. Set GSASII_DIR or pass "
        f"--gsasii-path. Checked: {locations}"
    )


def candidate_safety_errors(candidate: dict[str, Any]) -> list[str]:
    """Return hard-stop reasons that prevent selecting a final candidate."""
    convergence = candidate.get("convergence", {})
    correlation = candidate.get("correlations", {}).get("max_abs_percent")
    errors = []
    if candidate.get("status") != "succeeded":
        errors.append(f"status={candidate.get('status')}")
    if not convergence.get("converged", False):
        errors.append("not converged")
    if int(convergence.get("SVD0", 0) or 0):
        errors.append(f"SVD0={convergence.get('SVD0')}")
    max_shift = convergence.get("max_shift_over_su")
    if max_shift is None or float(max_shift) > 0.01:
        errors.append(f"max shift/s.u.={max_shift}")
    if correlation is not None and float(correlation) >= 95:
        errors.append(f"max correlation={correlation}%")
    if any(
        "nonpositive" in warning.lower()
        for warning in candidate.get("warnings", [])
    ):
        errors.append("nonpositive profile-width warning")
    return errors


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_clean(value: Any) -> Any:
    """Convert numpy and other scalar containers into JSON-safe values."""
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, TypeError):
            pass
    if isinstance(value, dict):
        return {str(key): json_clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_clean(item) for item in value]
    return value


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    temporary.write_text(
        json.dumps(json_clean(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def format_value_esd(value: float | None, esd: float | None) -> str:
    """Format value(esd) with crystallographic significant-digit rules."""
    if value is None:
        return "not available"
    if esd is None or not math.isfinite(esd) or esd <= 0:
        return f"{value:.8g}"
    magnitude = math.floor(math.log10(abs(esd)))
    leading = abs(esd) / (10**magnitude)
    significant_digits = 2 if leading < 3 else 1
    decimals = max(0, -magnitude + significant_digits - 1)
    uncertainty_digits = int(round(esd * (10**decimals)))
    return f"{value:.{decimals}f}({uncertainty_digits})"


def _last_float(pattern: str, text: str) -> float | None:
    matches = re.findall(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if not matches:
        return None
    value = matches[-1]
    if isinstance(value, tuple):
        raise TypeError("_last_float received a multi-group expression")
    return float(value)


def parse_lst(lst_path: Path) -> dict[str, Any]:
    """Extract explicitly labeled statistics from a GSAS-II listing file."""
    result: dict[str, Any] = {
        "path": str(lst_path),
        "exists": lst_path.is_file(),
        "sha256": sha256(lst_path) if lst_path.is_file() else None,
        "metrics": {},
        "durbin_watson": None,
        "warnings": [],
    }
    if not lst_path.is_file():
        result["warnings"].append("LST file is missing")
        return result
    text = lst_path.read_text(encoding="utf-8", errors="replace")
    other = re.findall(
        r"Other residuals:\s*R\s*=\s*([0-9.+-]+)%\s*,\s*"
        r"R-bkg\s*=\s*([0-9.+-]+)%\s*,\s*"
        r"wR-bkg\s*=\s*([0-9.+-]+)%\s*wRmin\s*=\s*([0-9.+-]+)%",
        text,
        flags=re.IGNORECASE,
    )
    if other:
        rp, r_bkg, wr_bkg, wr_min = map(float, other[-1])
        result["metrics"].update(
            {
                "Rp": {"value": rp, "source": "LST: Other residuals R"},
                "R_bkg": {"value": r_bkg, "source": "LST: R-bkg"},
                "wR_bkg": {"value": wr_bkg, "source": "LST: wR-bkg"},
                "wRmin": {"value": wr_min, "source": "LST: wRmin"},
            }
        )
    rf = re.findall(
        r"Final refinement RF,\s*RF\^2\s*=\s*([0-9.+-]+)%\s*,\s*([0-9.+-]+)%",
        text,
        flags=re.IGNORECASE,
    )
    if rf:
        rf_value, rf2_value = map(float, rf[-1])
        result["metrics"].update(
            {
                "RF": {"value": rf_value, "source": "LST: final RF"},
                "RF2": {"value": rf2_value, "source": "LST: final RF^2"},
            }
        )
    result["durbin_watson"] = _last_float(
        r"Durbin-Watson statistic\s*=\s*([0-9.+-]+)", text
    )
    warning_terms = (
        "singular",
        "svd",
        "highly correlated",
        "diverg",
        "not converged",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and any(term in stripped.lower() for term in warning_terms):
            if stripped not in result["warnings"]:
                result["warnings"].append(stripped)
    result["warnings"] = result["warnings"][:30]
    return result


def _metric(value: Any, source: str, definition: str) -> dict[str, Any]:
    return {
        "value": None if value is None else float(value),
        "source": source,
        "definition": definition,
    }


def _covariance_correlations(covariance: dict[str, Any]) -> dict[str, Any]:
    try:
        import numpy as np

        matrix = np.asarray(covariance.get("covMatrix"), dtype=float)
        names = list(covariance.get("varyList", []))
        if matrix.ndim != 2 or matrix.shape[0] != len(names):
            raise ValueError("covariance matrix and varyList do not align")
        diagonal = np.diag(matrix)
        all_pairs = []
        for row in range(len(names)):
            for column in range(row + 1, len(names)):
                denominator = math.sqrt(abs(diagonal[row] * diagonal[column]))
                if denominator == 0:
                    continue
                correlation = float(matrix[row, column] / denominator)
                all_pairs.append(
                    {
                        "parameter_1": names[row],
                        "parameter_2": names[column],
                        "correlation_percent": correlation * 100,
                    }
                )
        all_pairs.sort(
            key=lambda item: abs(item["correlation_percent"]), reverse=True
        )
        pairs = [
            item
            for item in all_pairs
            if abs(item["correlation_percent"]) >= 80
        ]
        return {
            "max_abs_percent": (
                abs(all_pairs[0]["correlation_percent"]) if all_pairs else 0.0
            ),
            "pairs_abs_ge_80_percent": pairs,
        }
    except Exception as exc:
        return {
            "max_abs_percent": None,
            "pairs_abs_ge_80_percent": [],
            "error": str(exc),
        }


def _instrument_parameters(
    instrument: dict[str, Any], covariance: dict[str, Any]
) -> dict[str, Any]:
    vary_list = list(covariance.get("varyList", []))
    sigmas = list(covariance.get("sig", []))
    sigma_by_name = dict(zip(vary_list, sigmas))
    output = {}
    for key in (
        "Lam1",
        "Lam2",
        "I(L2)/I(L1)",
        "Polariz.",
        "Zero",
        "U",
        "V",
        "W",
        "X",
        "Y",
        "Z",
        "SH/L",
    ):
        if key not in instrument:
            continue
        raw = instrument[key]
        value = raw[1] if isinstance(raw, (list, tuple)) and len(raw) > 1 else raw
        refined = (
            bool(raw[2])
            if isinstance(raw, (list, tuple)) and len(raw) > 2
            else False
        )
        sigma = None
        for name, candidate_sigma in sigma_by_name.items():
            if name.endswith(f":{key}"):
                sigma = float(candidate_sigma)
                break
        output[key] = {
            "value": float(value) if isinstance(value, (int, float)) or hasattr(value, "item") else value,
            "esd": sigma,
            "formatted": (
                format_value_esd(float(value), sigma)
                if isinstance(value, (int, float)) or hasattr(value, "item")
                else str(value)
            ),
            "refined": refined,
        }
    return output


def _residual_audit(histogram: Any, count: int = 10) -> dict[str, Any]:
    import numpy as np

    arrays = histogram.data["data"][1]
    x = np.asarray(arrays[0], dtype=float)
    observed = np.asarray(arrays[1], dtype=float)
    residual = np.asarray(arrays[5], dtype=float)
    finite = np.isfinite(x) & np.isfinite(observed) & np.isfinite(residual)
    x, observed, residual = x[finite], observed[finite], residual[finite]
    if residual.size < 3:
        return {"positive_local_maxima": [], "durbin_watson_unweighted": None}
    local = np.where(
        (residual[1:-1] >= residual[:-2])
        & (residual[1:-1] >= residual[2:])
        & (residual[1:-1] > 0)
    )[0] + 1
    ordered = local[np.argsort(residual[local])[::-1]]
    selected: list[int] = []
    for index in ordered:
        if all(abs(x[index] - x[kept]) >= 0.08 for kept in selected):
            selected.append(int(index))
        if len(selected) >= count:
            break
    denominator = float(np.sum(residual**2))
    dw = (
        float(np.sum(np.diff(residual) ** 2) / denominator)
        if denominator > 0
        else None
    )
    return {
        "positive_local_maxima": [
            {
                "two_theta": float(x[index]),
                "obs_minus_calc": float(residual[index]),
                "observed": float(observed[index]),
                "fraction_of_observed_percent": (
                    float(100 * residual[index] / observed[index])
                    if observed[index] != 0
                    else None
                ),
            }
            for index in selected
        ],
        "durbin_watson_unweighted": dw,
    }


def collect_candidate(
    gpx_path: Path,
    *,
    name: str,
    parent: str | None,
    releases: list[str],
    gsasii_path: Path,
) -> dict[str, Any]:
    """Load one GPX and return a machine-readable refinement audit."""
    gpx_path = gpx_path.expanduser().resolve()
    lst_path = gpx_path.with_suffix(".lst")
    if str(gsasii_path) not in sys.path:
        sys.path.insert(0, str(gsasii_path))
    from GSASII import GSASIIscriptable as G2sc  # type: ignore

    project = G2sc.G2Project(str(gpx_path))
    histograms = project.histograms()
    phases = project.phases()
    if len(histograms) != 1 or len(phases) != 1:
        raise ValueError(
            "Deterministic audit currently requires exactly one powder histogram "
            "and one phase"
        )
    histogram = histograms[0]
    phase = phases[0]
    covariance = project["Covariance"]["data"]
    rvals = covariance.get("Rvals", {})
    lst = parse_lst(lst_path)
    residuals = histogram.residuals
    metrics = {
        "Rwp": _metric(
            rvals.get("Rwp", residuals.get("wR")),
            "GPX Covariance.Rvals.Rwp",
            "weighted whole-pattern profile R factor",
        ),
        "GOF": _metric(
            rvals.get("GOF"),
            "GPX Covariance.Rvals.GOF",
            "sqrt(chi-square/(Nobs-Nvars))",
        ),
    }
    for key in ("Rp", "R_bkg", "wR_bkg", "RF", "RF2"):
        item = lst["metrics"].get(key)
        metrics[key] = _metric(
            item.get("value") if item else None,
            item.get("source") if item else "not available",
            {
                "Rp": "unweighted whole-pattern profile R factor",
                "R_bkg": "unweighted background-subtracted profile R factor",
                "wR_bkg": "weighted background-subtracted profile R factor",
                "RF": "Bragg structure-factor R factor",
                "RF2": "Bragg squared-structure-factor R factor",
            }[key],
        )
    cell_values, cell_esds = phase.get_cell_and_esd()
    cell = {
        key: {
            "value": float(value),
            "esd": float(cell_esds.get(key, 0.0)) or None,
            "formatted": format_value_esd(
                float(value), float(cell_esds.get(key, 0.0)) or None
            ),
        }
        for key, value in cell_values.items()
    }
    instrument = _instrument_parameters(
        histogram.data["Instrument Parameters"][0], covariance
    )
    correlations = _covariance_correlations(covariance)
    residual_audit = _residual_audit(histogram)
    warnings = list(lst["warnings"])
    converged = bool(rvals.get("converged", False))
    svd_zero = int(rvals.get("SVD0", 0) or 0)
    max_shift = rvals.get("Max shft/sig")
    if not converged:
        warnings.append("GSAS-II did not mark the candidate converged")
    if svd_zero:
        warnings.append(f"GSAS-II reported SVD0={svd_zero}")
    if max_shift is None or float(max_shift) > 0.01:
        warnings.append(
            "Maximum shift/s.u. exceeds 0.01; continue refinement before selection"
        )
    if correlations.get("max_abs_percent") is not None and correlations[
        "max_abs_percent"
    ] >= 95:
        warnings.append(
            "Parameter correlation at or above 95%; exploratory/reject unless constrained"
        )
    profile = {
        key: instrument[key]["value"]
        for key in ("U", "V", "W")
        if key in instrument
    }
    if len(profile) == 3:
        widths = []
        x_values = histogram.data["data"][1][0]
        for two_theta in (float(x_values[0]), float(x_values[-1])):
            tangent = math.tan(math.radians(two_theta / 2))
            widths.append(
                profile["U"] * tangent * tangent
                + profile["V"] * tangent
                + profile["W"]
            )
        if min(widths) <= 0:
            warnings.append(
                "Caglioti Gaussian width squared is nonpositive at a range endpoint"
            )
    return json_clean(
        {
            "name": name,
            "parent": parent,
            "releases": releases,
            "status": "succeeded",
            "gpx": {
                "path": str(gpx_path),
                "bytes": gpx_path.stat().st_size,
                "sha256": sha256(gpx_path),
            },
            "lst": lst,
            "histogram": histogram.name,
            "phase": phase.name,
            "metrics": metrics,
            "cell": cell,
            "instrument": instrument,
            "convergence": {
                "converged": converged,
                "SVD0": svd_zero,
                "max_shift_over_su": (
                    float(max_shift) if max_shift is not None else None
                ),
                "Nobs": rvals.get("Nobs"),
                "Nvars": rvals.get("Nvars"),
            },
            "correlations": correlations,
            "residual_audit": residual_audit,
            "durbin_watson": {
                "value": lst["durbin_watson"],
                "source": "LST" if lst["durbin_watson"] is not None else None,
                "unweighted_recalculation": residual_audit[
                    "durbin_watson_unweighted"
                ],
            },
            "warnings": list(dict.fromkeys(warnings)),
        }
    )
