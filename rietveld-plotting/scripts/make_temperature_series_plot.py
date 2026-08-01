#!/usr/bin/env python3
"""Plot a refined temperature or operando XRD sequence without changing GSAS-II.

The observed profiles are read from the hash-verified source patterns recorded
in ``sequential_results_<direction>.json``. Refined cell parameters and their
formal uncertainties are read from the same GSAS-II result export. No GPX is
opened, refined, or saved by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.ticker import AutoMinorLocator, MaxNLocator
import numpy as np


STYLE_PROFILE = "temperature-series-origin-v1"
OPERANDO_STYLE_PROFILE = "operando-series-origin-v1"
OUTPUT_DPI = 600
DEFAULT_FORMATS = ("png", "svg")
AXIS_LABEL_SIZE = 10.5
TICK_LABEL_SIZE = 8.2
ANNOTATION_SIZE = 7.0
PANEL_LABEL_SIZE = 12.0
AXIS_LINE_WIDTH = 1.0
PROFILE_LINE_WIDTH = 0.58
CELL_LINE_WIDTH = 0.65
CELL_MARKER_SIZE = 3.2
CELL_COLOR = "#b2182b"
INTENSITY_CMAP = "magma"

TEMPERATURE_CMAP = LinearSegmentedColormap.from_list(
    "muted_temperature",
    ["#234a84", "#6c9dc3", "#d9d9d9", "#d77a55", "#8b1a1a"],
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_name(value: str) -> str:
    keep: list[str] = []
    for character in value.strip():
        if character.isalnum() or character in "-_.":
            keep.append(character)
        elif character in " /\\:;,+()[]{}":
            keep.append("_")
    cleaned = "".join(keep).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "temperature_series"


def configure_style() -> str:
    available_fonts = {font.name for font in mpl.font_manager.fontManager.ttflist}
    base_font = "Arial" if "Arial" in available_fonts else "DejaVu Sans"
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [base_font],
            "font.size": ANNOTATION_SIZE,
            "axes.labelsize": AXIS_LABEL_SIZE,
            "axes.linewidth": AXIS_LINE_WIDTH,
            "xtick.labelsize": TICK_LABEL_SIZE,
            "ytick.labelsize": TICK_LABEL_SIZE,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.major.width": AXIS_LINE_WIDTH,
            "ytick.major.width": AXIS_LINE_WIDTH,
            "xtick.minor.width": 0.75,
            "ytick.minor.width": 0.75,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )
    return base_font


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Expected a JSON object in {path}")
    return payload


def resolve_audit_path(results_path: Path, requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    candidate = results_path.parent / "sequential_audit.json"
    if not candidate.is_file():
        raise SystemExit(
            "Sequential audit not found beside the results; pass --audit explicitly"
        )
    return candidate.resolve()


def validate_audit(audit: dict[str, Any], allow_failed: bool) -> str:
    status = str(audit.get("status", "")).lower()
    if status not in {"pass", "review", "fail"}:
        raise SystemExit("Sequential audit must declare status pass, review, or fail")
    if status == "fail" and not allow_failed:
        raise SystemExit(
            "Sequential audit status is fail; plotting stopped. Use the explicit "
            "--allow-failed-audit-for-diagnostic flag only for a labelled diagnostic figure."
        )
    return status


def verify_recorded_file(record: dict[str, Any], label: str) -> dict[str, Any]:
    path_value = record.get("path")
    expected = str(record.get("sha256", "")).lower()
    if not path_value or not expected:
        raise SystemExit(f"{label} lacks a recorded path or SHA-256")
    path = Path(str(path_value)).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label} not found: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise SystemExit(
            f"{label} SHA-256 mismatch: expected {expected}, observed {actual}"
        )
    return {"path": str(path), "sha256": actual, "bytes": path.stat().st_size}


def verify_gpx_records(results: dict[str, Any]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    segments = results.get("segments") or []
    if segments:
        for segment in segments:
            run = segment.get("run") or {}
            verified.append(
                verify_recorded_file(run, f"segment {segment.get('segment_id', '?')} GPX")
            )
        return verified

    gpx = results.get("gpx") or {}
    if str(gpx.get("path", "")).startswith("segmented:"):
        raise SystemExit("Segmented results are missing segment run records")
    verified.append(verify_recorded_file(gpx, "sequential GPX"))
    return verified


def read_pattern(path: Path) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Read common integrated XRD text formats without altering intensities.

    GSAS FXYE stores 2theta in centidegrees, so only that documented unit
    conversion is applied. Generic XY/XYE text is used as written.
    """
    x_values: list[float] = []
    y_values: list[float] = []
    fxye_units = False
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("BANK ") and "FXYE" in upper:
                fxye_units = True
                continue
            if line.startswith(("#", "!", ";", "//")):
                continue
            fields = line.replace(",", " ").split()
            if len(fields) < 2:
                continue
            try:
                x_value = float(fields[0])
                y_value = float(fields[1])
            except ValueError:
                continue
            if math.isfinite(x_value) and math.isfinite(y_value):
                x_values.append(x_value)
                y_values.append(y_value)
    if len(x_values) < 3:
        raise SystemExit(f"Integrated pattern has fewer than three numeric rows: {path}")
    x = np.asarray(x_values, dtype=float)
    y = np.asarray(y_values, dtype=float)
    if fxye_units:
        x = x / 100.0
    order = np.argsort(x)
    x = x[order]
    y = y[order]
    unique = np.concatenate(([True], np.diff(x) > 0))
    x = x[unique]
    y = y[unique]
    if x.size < 3:
        raise SystemExit(f"Integrated pattern has fewer than three unique x values: {path}")
    return x, y, {"fxye_centidegree_conversion": fxye_units}


def series_axis_label(
    key: str,
    label_override: str | None = None,
    unit_override: str | None = None,
) -> tuple[str, str]:
    if label_override:
        return label_override, unit_override or ""
    lowered = key.lower()
    if lowered.endswith("_k") or lowered == "temperature":
        return "Temperature (K)", "K"
    if lowered.endswith("_c"):
        return "Temperature (°C)", "°C"
    if lowered in {"source_frame", "frame", "frame_id", "order", "scan"}:
        return "Frame", unit_override or ""
    if lowered in {"time_min", "time_minute", "time_minutes"}:
        return "Time (min)", unit_override or "min"
    if lowered in {"time_s", "time_sec", "time_seconds"}:
        return "Time (s)", unit_override or "s"
    if lowered in {"voltage_v", "potential_v", "voltage", "potential"}:
        return "Voltage (V)", unit_override or "V"
    return key.replace("_", " "), ""


def load_frames(
    results: dict[str, Any],
    series_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_frames = results.get("frames")
    if not isinstance(raw_frames, list) or len(raw_frames) < 2:
        raise SystemExit("Sequential results must contain at least two frames")
    frames = sorted(raw_frames, key=lambda item: int(item.get("order", 0)))
    series_values: list[float] = []
    verified_patterns: list[dict[str, Any]] = []
    for frame in frames:
        metadata = frame.get("metadata") or {}
        value = frame.get("order") if series_key == "order" else metadata.get(series_key)
        try:
            series_value = float(value)
        except (TypeError, ValueError) as exc:
            raise SystemExit(
                f"Frame {frame.get('frame_id', '?')} lacks numeric coordinate {series_key}"
            ) from exc
        if not math.isfinite(series_value):
            raise SystemExit(f"Frame {frame.get('frame_id', '?')} has non-finite series coordinate")
        series_values.append(series_value)
        record = verify_recorded_file(
            frame.get("pattern") or {}, f"frame {frame.get('frame_id', '?')} pattern"
        )
        x, y, parser = read_pattern(Path(record["path"]))
        frame["_series_value"] = series_value
        frame["_x"] = x
        frame["_y"] = y
        frame["_parser"] = parser
        verified_patterns.append(record)
    if max(series_values) == min(series_values):
        raise SystemExit(
            f"Coordinate {series_key} is constant; select a varying metadata field or order"
        )
    return frames, verified_patterns


def apply_x_window(
    frames: list[dict[str, Any]], x_min: float | None, x_max: float | None
) -> tuple[float, float]:
    overlap_min = max(float(np.min(frame["_x"])) for frame in frames)
    overlap_max = min(float(np.max(frame["_x"])) for frame in frames)
    lower = overlap_min if x_min is None else float(x_min)
    upper = overlap_max if x_max is None else float(x_max)
    if lower < overlap_min - 1e-9 or upper > overlap_max + 1e-9:
        raise SystemExit(
            f"Requested 2theta window [{lower}, {upper}] exceeds the common frame range "
            f"[{overlap_min}, {overlap_max}]"
        )
    if upper <= lower:
        raise SystemExit("The selected 2theta upper bound must exceed the lower bound")
    for frame in frames:
        mask = (frame["_x"] >= lower) & (frame["_x"] <= upper)
        if np.count_nonzero(mask) < 3:
            raise SystemExit(f"Frame {frame.get('frame_id')} has too few points in the x window")
        frame["_x_visible"] = frame["_x"][mask]
        frame["_y_visible"] = frame["_y"][mask]
    return lower, upper


def intensity_transform(
    frames: list[dict[str, Any]], mode: str
) -> dict[str, Any]:
    all_y = np.concatenate([frame["_y_visible"] for frame in frames])
    global_min = float(np.min(all_y))
    global_max = float(np.max(all_y))
    global_span = global_max - global_min
    if global_span <= 0:
        raise SystemExit("Sequential-series intensity span is zero")
    for frame in frames:
        y = frame["_y_visible"]
        if mode == "raw":
            display = y.copy()
        elif mode == "global":
            display = (y - global_min) / global_span
        elif mode == "per-frame":
            local_span = float(np.max(y) - np.min(y))
            if local_span <= 0:
                raise SystemExit(f"Frame {frame.get('frame_id')} has zero intensity span")
            display = (y - float(np.min(y))) / local_span
        else:
            raise SystemExit(f"Unsupported intensity mode: {mode}")
        frame["_display_y"] = display
    return {
        "mode": mode,
        "smoothed": False,
        "global_min": global_min,
        "global_max": global_max,
        "preserves_between_frame_scale": mode in {"raw", "global"},
    }


def series_norm(frames: list[dict[str, Any]]) -> Normalize:
    values = np.asarray([frame["_series_value"] for frame in frames], dtype=float)
    return Normalize(vmin=float(np.min(values)), vmax=float(np.max(values)))


def style_axes(ax: plt.Axes) -> None:
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(AXIS_LINE_WIDTH)
        spine.set_color("black")
    ax.tick_params(which="major", length=4.5, pad=2.5, top=False, right=False)
    ax.tick_params(which="minor", length=2.5, top=False, right=False)
    ax.xaxis.set_minor_locator(AutoMinorLocator(2))
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    ax.grid(False)


def diagnostic_label(fig: plt.Figure, audit_status: str) -> None:
    if audit_status == "fail":
        fig.text(
            0.5,
            0.995,
            "DIAGNOSTIC ONLY — SEQUENTIAL AUDIT FAIL",
            ha="center",
            va="top",
            color="#b2182b",
            fontsize=8.5,
            fontweight="bold",
        )


def plot_stacked(
    frames: list[dict[str, Any]],
    x_limits: tuple[float, float],
    series_key: str,
    series_label: str | None,
    series_unit: str | None,
    offset_step: float,
    max_labels: int,
    audit_status: str,
) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(5.2, 4.2), dpi=180)
    norm = series_norm(frames)
    is_temperature = series_key.lower().startswith("temperature")
    color_map = TEMPERATURE_CMAP if is_temperature else mpl.colormaps["viridis"]
    offsets = np.arange(len(frames), dtype=float) * offset_step
    for index, frame in enumerate(frames):
        ax.plot(
            frame["_x_visible"],
            frame["_display_y"] + offsets[index],
            color=color_map(norm(frame["_series_value"])),
            lw=PROFILE_LINE_WIDTH,
            solid_capstyle="round",
        )
    _, unit = series_axis_label(series_key, series_label, series_unit)
    labels = [
        f"{frame['_series_value']:g}{(' ' + unit) if unit else ''}" for frame in frames
    ]
    ax.set_yticks([])
    ax.set_xlim(*x_limits)
    top = offsets[-1] + max(3.0, offset_step * 5.0)
    lower = min(float(np.min(frame["_display_y"])) for frame in frames)
    ax.set_ylim(lower - 0.06 * max(1.0, top - lower), top)
    ax.set_xlabel(r"2$\theta$ (Degree)")
    ax.set_ylabel("Intensity (offset)")
    style_axes(ax)
    ax.yaxis.set_minor_locator(mpl.ticker.NullLocator())
    label_count = min(len(frames), max_labels)
    label_indices = sorted(
        {int(round(value)) for value in np.linspace(0, len(frames) - 1, label_count)}
    )
    for index in label_indices:
        offset = offsets[index]
        label = labels[index]
        ax.text(
            0.992,
            offset + 0.025 * offset_step,
            label,
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="bottom",
            fontsize=6.4,
            color="black",
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 0.25, "alpha": 0.82},
            clip_on=True,
        )
    fig.subplots_adjust(left=0.17, right=0.975, bottom=0.14, top=0.965)
    diagnostic_label(fig, audit_status)
    return fig


def common_grid(frames: list[dict[str, Any]], x_limits: tuple[float, float]) -> np.ndarray:
    steps = []
    for frame in frames:
        delta = np.diff(frame["_x_visible"])
        delta = delta[delta > 0]
        if delta.size:
            steps.append(float(np.median(delta)))
    if not steps:
        raise SystemExit("Cannot determine a common 2theta grid")
    step = max(min(steps), (x_limits[1] - x_limits[0]) / 4000.0)
    count = min(4001, max(3, int(round((x_limits[1] - x_limits[0]) / step)) + 1))
    return np.linspace(x_limits[0], x_limits[1], count)


def is_strictly_monotonic(values: np.ndarray) -> bool:
    differences = np.diff(values)
    return bool(np.all(differences > 0) or np.all(differences < 0))


def plot_contour(
    frames: list[dict[str, Any]],
    x_limits: tuple[float, float],
    series_key: str,
    series_label: str | None,
    series_unit: str | None,
    vmax_percentile: float,
    audit_status: str,
) -> tuple[plt.Figure, dict[str, Any]]:
    grid = common_grid(frames, x_limits)
    matrix = np.vstack(
        [np.interp(grid, frame["_x_visible"], frame["_display_y"]) for frame in frames]
    )
    series_values = np.asarray([frame["_series_value"] for frame in frames], dtype=float)
    monotonic = is_strictly_monotonic(series_values)
    y = series_values if monotonic else np.arange(len(frames), dtype=float)
    y_label = series_axis_label(series_key, series_label, series_unit)[0] if monotonic else "Frame order"
    vmin = float(np.min(matrix))
    vmax = float(np.percentile(matrix, vmax_percentile))
    if vmax <= vmin:
        vmax = float(np.max(matrix))
    fig, ax = plt.subplots(figsize=(5.2, 4.2), dpi=180)
    mesh = ax.pcolormesh(
        grid,
        y,
        matrix,
        shading="auto",
        cmap=INTENSITY_CMAP,
        vmin=vmin,
        vmax=vmax,
        rasterized=True,
    )
    ax.set_xlim(*x_limits)
    ax.set_xlabel(r"2$\theta$ (Degree)")
    ax.set_ylabel(y_label)
    style_axes(ax)
    colorbar = fig.colorbar(mesh, ax=ax, pad=0.025, fraction=0.045)
    colorbar.set_label("Relative intensity", fontsize=AXIS_LABEL_SIZE)
    colorbar.ax.tick_params(labelsize=TICK_LABEL_SIZE, width=0.8, length=3)
    fig.subplots_adjust(left=0.15, right=0.89, bottom=0.14, top=0.965)
    diagnostic_label(fig, audit_status)
    return fig, {
        "common_grid_points": int(grid.size),
        "interpolation": "linear resampling onto common 2theta grid; no smoothing",
        "vertical_axis": series_key if monotonic else "frame_order",
        "vmax_percentile": vmax_percentile,
        "vmin": vmin,
        "vmax": vmax,
    }


def select_phase(frames: list[dict[str, Any]], requested: str | None) -> str:
    phase_sets = [set((frame.get("cells") or {}).keys()) for frame in frames]
    common = set.intersection(*phase_sets) if phase_sets else set()
    if requested:
        if requested not in common:
            raise SystemExit(
                f"Requested phase {requested!r} is not present in every frame; common phases: {sorted(common)}"
            )
        return requested
    if len(common) == 1:
        return next(iter(common))
    raise SystemExit(
        "Multiple refined phases are present; select one explicitly with --phase. "
        f"Common phases: {sorted(common)}"
    )


def select_cell_parameters(
    frames: list[dict[str, Any]], phase: str, requested: str
) -> list[str]:
    first = (frames[0].get("cells") or {}).get(phase) or {}
    if requested != "auto":
        parameters = [item.strip() for item in requested.split(",") if item.strip()]
    else:
        parameters = [
            name
            for name in ("a", "b", "c", "alpha", "beta", "gamma")
            if bool((first.get(name) or {}).get("symmetry_independent"))
        ]
        if "volume" in first:
            parameters.append("volume")
    if not parameters:
        raise SystemExit(f"No plottable cell parameters found for phase {phase}")
    for parameter in parameters:
        for frame in frames:
            payload = ((frame.get("cells") or {}).get(phase) or {}).get(parameter)
            if not isinstance(payload, dict) or payload.get("value") is None:
                raise SystemExit(
                    f"Cell parameter {phase}.{parameter} is missing in frame {frame.get('frame_id')}"
                )
    return parameters


def parameter_label(parameter: str) -> str:
    if parameter == "volume":
        return r"$V$ ($\mathring{\mathrm{A}}^3$)"
    if parameter in {"alpha", "beta", "gamma"}:
        symbol = {"alpha": r"\alpha", "beta": r"\beta", "gamma": r"\gamma"}[parameter]
        return rf"${symbol}$ (Degree)"
    return rf"${parameter}$ ($\mathring{{\mathrm{{A}}}}$)"


def plot_cells(
    frames: list[dict[str, Any]],
    phase: str,
    parameters: list[str],
    series_key: str,
    series_label: str | None,
    series_unit: str | None,
    audit_status: str,
) -> plt.Figure:
    count = len(parameters)
    columns = 1 if count == 1 else (2 if count <= 4 else 3)
    rows = int(math.ceil(count / columns))
    if count == 1:
        figure_size = (5.2, 3.3)
    elif columns == 2:
        figure_size = (5.6, 2.65 * rows)
    else:
        figure_size = (6.6, 2.55 * rows)
    fig, axes = plt.subplots(rows, columns, figsize=figure_size, dpi=180, squeeze=False)
    series_values = np.asarray([frame["_series_value"] for frame in frames], dtype=float)
    flat_axes = list(axes.flat)
    for index, parameter in enumerate(parameters):
        ax = flat_axes[index]
        payloads = [((frame.get("cells") or {})[phase])[parameter] for frame in frames]
        values = np.asarray([float(item["value"]) for item in payloads], dtype=float)
        esds = np.asarray(
            [float(item["esd"]) if item.get("esd") is not None else np.nan for item in payloads],
            dtype=float,
        )
        finite_esd = np.isfinite(esds)
        ax.plot(
            series_values,
            values,
            color="#333333",
            lw=CELL_LINE_WIDTH,
            zorder=1,
        )
        ax.plot(
            series_values,
            values,
            linestyle="None",
            marker="o",
            markersize=CELL_MARKER_SIZE,
            markerfacecolor="white",
            markeredgecolor=CELL_COLOR,
            markeredgewidth=0.75,
            zorder=3,
        )
        if np.any(finite_esd):
            ax.errorbar(
                series_values[finite_esd],
                values[finite_esd],
                yerr=esds[finite_esd],
                fmt="none",
                ecolor=CELL_COLOR,
                elinewidth=0.45,
                capsize=1.3,
                capthick=0.45,
                zorder=2,
            )
        ax.set_xlabel(series_axis_label(series_key, series_label, series_unit)[0])
        ax.set_ylabel(parameter_label(parameter))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.ticklabel_format(axis="y", style="plain", useOffset=False)
        style_axes(ax)
        panel = chr(ord("a") + index)
        ax.text(
            -0.18,
            1.03,
            panel,
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=PANEL_LABEL_SIZE,
            fontweight="bold",
            clip_on=False,
        )
    for ax in flat_axes[count:]:
        ax.set_visible(False)
    fig.suptitle(phase, y=0.995, fontsize=9.0, fontweight="normal")
    fig.subplots_adjust(
        left=0.13,
        right=0.98,
        bottom=0.12,
        top=0.93,
        wspace=0.43,
        hspace=0.43,
    )
    diagnostic_label(fig, audit_status)
    return fig


def save_figure(
    fig: plt.Figure, out_dir: Path, stem: str, formats: list[str]
) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for format_name in formats:
        suffix = format_name.lower().lstrip(".")
        if suffix not in {"png", "svg", "pdf", "tif", "tiff"}:
            raise SystemExit(f"Unsupported output format: {format_name}")
        path = out_dir / f"{stem}.{suffix}"
        kwargs = {"dpi": OUTPUT_DPI} if suffix in {"png", "tif", "tiff"} else {}
        fig.savefig(path, bbox_inches="tight", pad_inches=0.035, **kwargs)
        outputs[suffix] = str(path)
    plt.close(fig)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, help="sequential_results_<direction>.json")
    parser.add_argument("--audit", help="sequential_audit.json; defaults beside --results")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample-id", help="Output stem prefix; defaults to results parent run name")
    parser.add_argument("--phase", help="Phase used for cell-parameter panels; required for multiphase data")
    parser.add_argument(
        "--series-key",
        help="Varying metadata coordinate such as source_frame, time_min, or temperature_K",
    )
    parser.add_argument(
        "--temperature-key",
        help="Backward-compatible alias for --series-key; defaults to temperature_K when neither is set",
    )
    parser.add_argument("--series-label", help="Explicit axis label for the selected series coordinate")
    parser.add_argument("--series-unit", help="Optional unit appended to stacked-profile annotations")
    parser.add_argument("--cell-parameters", default="auto", help="auto or comma-separated names")
    parser.add_argument("--x-min", type=float)
    parser.add_argument("--x-max", type=float)
    parser.add_argument(
        "--intensity-mode",
        choices=("global", "raw", "per-frame"),
        default="global",
        help="global preserves between-frame intensity scale; per-frame is display-only normalization",
    )
    parser.add_argument("--stack-offset", type=float, default=0.62)
    parser.add_argument("--stack-max-labels", type=int, default=24)
    parser.add_argument("--contour-vmax-percentile", type=float, default=99.5)
    parser.add_argument("--formats", default=",".join(DEFAULT_FORMATS))
    parser.add_argument(
        "--allow-failed-audit-for-diagnostic",
        action="store_true",
        help="Allow a fail result only with a permanent diagnostic label on every image",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.stack_offset <= 0:
        raise SystemExit("--stack-offset must be positive")
    if args.stack_max_labels < 1:
        raise SystemExit("--stack-max-labels must be at least 1")
    if not 90.0 <= args.contour_vmax_percentile <= 100.0:
        raise SystemExit("--contour-vmax-percentile must be between 90 and 100")

    results_path = Path(args.results).expanduser().resolve()
    if not results_path.is_file():
        raise SystemExit(f"Sequential results not found: {results_path}")
    audit_path = resolve_audit_path(results_path, args.audit)
    results_hash_before = sha256_file(results_path)
    audit_hash_before = sha256_file(audit_path)
    results = load_json(results_path)
    audit = load_json(audit_path)
    audit_status = validate_audit(audit, args.allow_failed_audit_for_diagnostic)

    if args.series_key and args.temperature_key and args.series_key != args.temperature_key:
        raise SystemExit("Use only one coordinate: --series-key or --temperature-key")
    series_key = args.series_key or args.temperature_key or "temperature_K"
    is_temperature_series = series_key.lower().startswith("temperature")
    route_stem = "temperature" if is_temperature_series else "operando"

    direction = str(results.get("direction", ""))
    if direction not in {"forward", "reverse"}:
        raise SystemExit("Sequential results direction must be forward or reverse")
    frames, verified_patterns = load_frames(results, series_key)
    verified_gpx = verify_gpx_records(results)
    x_limits = apply_x_window(frames, args.x_min, args.x_max)
    intensity_record = intensity_transform(frames, args.intensity_mode)
    phase = select_phase(frames, args.phase)
    cell_parameters = select_cell_parameters(frames, phase, args.cell_parameters)
    base_font = configure_style()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    default_sample = results_path.parent.parent.name or f"{route_stem}_series"
    sample_id = clean_name(args.sample_id or default_sample)
    formats = [item.strip().lower() for item in args.formats.split(",") if item.strip()]
    if not formats:
        raise SystemExit("At least one output format is required")

    stacked_outputs = save_figure(
        plot_stacked(
            frames,
            x_limits,
            series_key,
            args.series_label,
            args.series_unit,
            args.stack_offset,
            args.stack_max_labels,
            audit_status,
        ),
        out_dir,
        f"{sample_id}_{route_stem}_stacked",
        formats,
    )
    contour_figure, contour_record = plot_contour(
        frames,
        x_limits,
        series_key,
        args.series_label,
        args.series_unit,
        args.contour_vmax_percentile,
        audit_status,
    )
    contour_outputs = save_figure(
        contour_figure,
        out_dir,
        f"{sample_id}_{route_stem}_contour",
        formats,
    )
    cell_outputs = save_figure(
        plot_cells(
            frames,
            phase,
            cell_parameters,
            series_key,
            args.series_label,
            args.series_unit,
            audit_status,
        ),
        out_dir,
        f"{sample_id}_{clean_name(phase)}_cell_{route_stem}",
        formats,
    )

    results_hash_after = sha256_file(results_path)
    audit_hash_after = sha256_file(audit_path)
    if results_hash_after != results_hash_before or audit_hash_after != audit_hash_before:
        raise SystemExit("Input integrity failure: results or audit JSON changed during plotting")
    for record in verified_patterns + verified_gpx:
        if sha256_file(Path(record["path"])) != record["sha256"]:
            raise SystemExit(f"Input integrity failure after plotting: {record['path']}")

    series_values = np.asarray([frame["_series_value"] for frame in frames], dtype=float)
    coordinate_record = {
        "metadata_key": series_key,
        "label": series_axis_label(series_key, args.series_label, args.series_unit)[0],
        "minimum": float(np.min(series_values)),
        "maximum": float(np.max(series_values)),
        "monotonic_in_acquisition_order": is_strictly_monotonic(series_values),
    }
    manifest = {
        "schema_version": 1,
        "style_profile": STYLE_PROFILE if is_temperature_series else OPERANDO_STYLE_PROFILE,
        "sample_id": sample_id,
        "direction": direction,
        "audit_status": audit_status,
        "diagnostic_only": audit_status == "fail",
        "sources": {
            "results": {"path": str(results_path), "sha256": results_hash_before},
            "audit": {"path": str(audit_path), "sha256": audit_hash_before},
            "gpx": verified_gpx,
            "patterns": verified_patterns,
        },
        "frame_count": len(frames),
        "series_coordinate": coordinate_record,
        "x_range": list(x_limits),
        "intensity_display": intensity_record,
        "stacked_display": {
            "vertical_position": "uniform acquisition-order offsets",
            "series_labels": "sampled per-frame annotations; not a proportional y coordinate",
            "offset_step": args.stack_offset,
            "maximum_annotation_count": args.stack_max_labels,
        },
        "contour_display": contour_record,
        "cell_plot": {
            "phase": phase,
            "parameters": cell_parameters,
            "formal_esd_error_bars": True,
        },
        "style": {
            "font": base_font,
            "axis_label_size_pt": AXIS_LABEL_SIZE,
            "tick_label_size_pt": TICK_LABEL_SIZE,
            "annotation_size_pt": ANNOTATION_SIZE,
            "profile_line_width_pt": PROFILE_LINE_WIDTH,
            "output_dpi": OUTPUT_DPI,
            "white_background": True,
            "boxed_axes": True,
            "grid": False,
        },
        "outputs": {
            "stacked": stacked_outputs,
            "contour": contour_outputs,
            "cell_series": cell_outputs,
        },
        "integrity": "all source hashes identical before and after plotting",
    }
    if is_temperature_series:
        manifest["temperature"] = coordinate_record
    manifest_path = out_dir / f"{sample_id}_{route_stem}_plot_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"outputs": manifest["outputs"], "manifest": str(manifest_path)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
