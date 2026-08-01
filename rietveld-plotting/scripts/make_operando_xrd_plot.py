#!/usr/bin/env python3
"""Plot hash-verified experimental operando XRD with synchronized voltage.

This route is deliberately separate from sequential Rietveld plotting. It
accepts only a conversion audit plus a synchronized frame manifest, verifies
the recorded source and converted-pattern hashes, and never opens a GPX.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D

from make_temperature_series_plot import (
    ANNOTATION_SIZE,
    AXIS_LABEL_SIZE,
    OUTPUT_DPI,
    TICK_LABEL_SIZE,
    clean_name,
    configure_style,
    read_pattern,
    sha256_file,
    style_axes,
)


STYLE_PROFILE = "experimental-operando-origin-v1"
CHARGE_COLOR = "#d73027"
DISCHARGE_COLOR = "#2166ac"
HOLD_COLOR = "#666666"


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read {label} JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"{label} JSON must contain an object")
    return payload


def require_hash(record: dict[str, Any], label: str) -> dict[str, Any]:
    path_value = record.get("path")
    expected = str(record.get("sha256", "")).lower()
    if not path_value or not expected:
        raise SystemExit(f"{label} lacks path or SHA-256")
    path = Path(str(path_value)).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"{label} not found: {path}")
    observed = sha256_file(path)
    if observed != expected:
        raise SystemExit(
            f"{label} SHA-256 mismatch: expected {expected}, observed {observed}"
        )
    return {"path": str(path), "sha256": observed, "bytes": path.stat().st_size}


def verified_conversion_frames(
    audit: dict[str, Any], verify_raw_sources: bool
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if audit.get("format") != "STOE WinXPOW RAW_1.06Powdat":
        raise SystemExit("Conversion audit is not the supported STOE RAW_1.06 format")
    if bool(audit.get("smoothing_performed")):
        raise SystemExit("Conversion audit reports smoothing; plotting stopped")
    if bool(audit.get("background_subtraction_performed")):
        raise SystemExit("Conversion audit reports background subtraction; plotting stopped")
    raw_frames = audit.get("frames")
    if not isinstance(raw_frames, list) or len(raw_frames) < 2:
        raise SystemExit("Conversion audit must contain at least two converted frames")
    frames: dict[str, dict[str, Any]] = {}
    verified: list[dict[str, Any]] = []
    for frame in raw_frames:
        frame_id = str(frame.get("frame_id", ""))
        if not frame_id or frame_id in frames:
            raise SystemExit(f"Invalid or duplicate conversion frame id: {frame_id!r}")
        output = require_hash(frame.get("output") or {}, f"converted frame {frame_id}")
        source = None
        if verify_raw_sources:
            source = require_hash(frame.get("source") or {}, f"raw source {frame_id}")
        frames[frame_id] = {**frame, "_output": output, "_source": source}
        verified.append(output)
        if source:
            verified.append(source)
    if int(audit.get("converted_count", -1)) != len(frames):
        raise SystemExit("Conversion audit frame count does not match its frame records")
    return frames, verified


def read_manifest(path: Path) -> list[dict[str, Any]]:
    required = {
        "frame_id",
        "order",
        "pattern_path",
        "time_s",
        "current_mA",
        "voltage_V",
        "sync_delta_s",
    }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                missing = sorted(required - set(reader.fieldnames or []))
                raise SystemExit(f"Operando manifest is missing columns: {missing}")
            rows = list(reader)
    except OSError as exc:
        raise SystemExit(f"Cannot read operando manifest {path}: {exc}") from exc
    if len(rows) < 2:
        raise SystemExit("Operando manifest must contain at least two frames")
    parsed: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = {
                **row,
                "order": int(row["order"]),
                "time_s": float(row["time_s"]),
                "current_mA": float(row["current_mA"]),
                "voltage_V": float(row["voltage_V"]),
                "sync_delta_s": float(row["sync_delta_s"]),
            }
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Non-numeric operando metadata in frame {row.get('frame_id')}") from exc
        numeric = (item["time_s"], item["current_mA"], item["voltage_V"], item["sync_delta_s"])
        if not all(math.isfinite(value) for value in numeric):
            raise SystemExit(f"Non-finite operando metadata in frame {row.get('frame_id')}")
        parsed.append(item)
    parsed.sort(key=lambda item: item["order"])
    if [item["order"] for item in parsed] != list(range(len(parsed))):
        raise SystemExit("Operando manifest order must be contiguous from zero")
    times = np.asarray([item["time_s"] for item in parsed])
    if not np.all(np.diff(times) > 0):
        raise SystemExit("Operando manifest time_s must increase strictly")
    return parsed


def validate_sync_binding(
    sync: dict[str, Any],
    manifest_path: Path,
    manifest_sha256: str,
    rows: list[dict[str, Any]],
) -> None:
    if sync.get("mode") != "nearest_time":
        raise SystemExit(
            "Experimental-operando plotting requires a nearest-time synchronization audit"
        )
    if bool(sync.get("interpolation_performed")):
        raise SystemExit("Synchronization audit reports interpolation; plotting stopped")
    if int(sync.get("matched_count", -1)) != len(rows) or sync.get(
        "unmatched_frame_ids"
    ):
        raise SystemExit("Synchronization audit does not show a complete frame match")
    if int(sync.get("frame_count", -1)) != len(rows):
        raise SystemExit("Synchronization audit frame count does not match the manifest")

    recorded_manifest_path = sync.get("output_manifest")
    recorded_manifest_hash = str(sync.get("output_manifest_sha256", "")).lower()
    if not recorded_manifest_path or not recorded_manifest_hash:
        raise SystemExit(
            "Synchronization audit does not bind its output manifest path and SHA-256"
        )
    if Path(str(recorded_manifest_path)).expanduser().resolve() != manifest_path:
        raise SystemExit("Synchronization audit is bound to a different manifest path")
    if recorded_manifest_hash != manifest_sha256:
        raise SystemExit("Synchronization audit manifest SHA-256 does not match")

    maximum_delta = float(sync.get("maximum_delta_s", -math.inf))
    maximum_observed = float(sync.get("maximum_absolute_sync_delta_s", math.inf))
    if maximum_delta <= 0 or maximum_observed > maximum_delta:
        raise SystemExit("Synchronization audit exceeds its declared maximum time delta")
    row_deltas = [abs(float(row["sync_delta_s"])) for row in rows]
    if row_deltas and not math.isclose(
        max(row_deltas), maximum_observed, rel_tol=1e-9, abs_tol=1e-9
    ):
        raise SystemExit(
            "Synchronization audit maximum delta does not match the manifest rows"
        )
    matches = sync.get("matches")
    if not isinstance(matches, list) or len(matches) != len(rows):
        raise SystemExit("Synchronization audit lacks one match record per manifest frame")
    match_ids = [str(match.get("frame_id", "")) for match in matches]
    row_ids = [str(row["frame_id"]) for row in rows]
    if match_ids != row_ids:
        raise SystemExit("Synchronization match records do not follow the manifest frames")
    metadata_indices = [match.get("metadata_row_index") for match in matches]
    if any(not isinstance(index, int) for index in metadata_indices) or len(
        metadata_indices
    ) != len(set(metadata_indices)):
        raise SystemExit("Synchronization audit reuses a metadata row")


def bind_and_read_patterns(
    rows: list[dict[str, Any]], conversions: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(rows) != len(conversions):
        raise SystemExit("Manifest and conversion audit contain different frame counts")
    bound: list[dict[str, Any]] = []
    for row in rows:
        frame_id = str(row["frame_id"])
        if frame_id not in conversions:
            raise SystemExit(f"Manifest frame {frame_id} is absent from conversion audit")
        conversion = conversions[frame_id]
        manifest_path = Path(str(row["pattern_path"])).expanduser().resolve()
        audited_path = Path(conversion["_output"]["path"])
        if manifest_path != audited_path:
            raise SystemExit(f"Manifest path differs from conversion audit for {frame_id}")
        x, y, parser = read_pattern(audited_path)
        bound.append({**row, "_x": x, "_y": y, "_parser": parser})
    return bound


def common_matrix(
    frames: list[dict[str, Any]], x_min: float | None, x_max: float | None
) -> tuple[np.ndarray, np.ndarray, tuple[float, float], str]:
    overlap_min = max(float(np.min(frame["_x"])) for frame in frames)
    overlap_max = min(float(np.max(frame["_x"])) for frame in frames)
    lower = overlap_min if x_min is None else float(x_min)
    upper = overlap_max if x_max is None else float(x_max)
    if lower < overlap_min - 1e-9 or upper > overlap_max + 1e-9 or upper <= lower:
        raise SystemExit(
            f"Requested 2theta range [{lower}, {upper}] is outside the common range "
            f"[{overlap_min}, {overlap_max}]"
        )
    steps = [float(np.median(np.diff(frame["_x"]))) for frame in frames]
    step = max(min(steps), (upper - lower) / 4000.0)
    points = min(4001, max(3, int(round((upper - lower) / step)) + 1))
    grid = np.linspace(lower, upper, points)
    matrix = np.vstack([np.interp(grid, frame["_x"], frame["_y"]) for frame in frames])
    return grid, matrix, (lower, upper), "linear resampling to common 2theta grid; no smoothing"


def transform_intensity(
    matrix: np.ndarray, mode: str, clip_percentile: float
) -> tuple[np.ndarray, dict[str, Any]]:
    if mode == "raw":
        display = matrix.copy()
        description = "raw counts"
    elif mode == "log":
        display = np.log1p(np.clip(matrix, 0.0, None))
        description = "log1p of non-negative counts"
    elif mode == "sqrt":
        display = np.sqrt(np.clip(matrix, 0.0, None))
        description = "square root of non-negative counts"
    elif mode == "per-frame":
        low = np.percentile(matrix, 2.0, axis=1, keepdims=True)
        high = np.percentile(matrix, 99.5, axis=1, keepdims=True)
        span = high - low
        if np.any(span <= 0):
            raise SystemExit("At least one frame has no robust intensity span")
        display = np.clip((matrix - low) / span, 0.0, 1.0)
        description = "per-frame 2nd-to-99.5th percentile display normalization"
    else:
        raise SystemExit(f"Unsupported intensity mode: {mode}")
    vmax = float(np.percentile(display, clip_percentile))
    vmin = float(np.min(display))
    if vmax <= vmin:
        raise SystemExit("Displayed operando intensity has zero span")
    return display, {
        "mode": mode,
        "description": description,
        "smoothing_performed": False,
        "background_subtraction_performed": False,
        "clip_percentile": clip_percentile,
        "vmin": vmin,
        "vmax": vmax,
        "preserves_between_frame_scale": mode in {"raw", "log", "sqrt"},
    }


def electrochemical_segments(current: np.ndarray) -> tuple[np.ndarray, float]:
    threshold = max(1e-9, float(np.max(np.abs(current))) * 0.05)
    states = np.zeros(current.shape, dtype=int)
    states[current > threshold] = 1
    states[current < -threshold] = -1
    return states, threshold


def parse_x_windows(
    value: str | None, x_limits: tuple[float, float]
) -> list[tuple[float, float]]:
    """Parse ordered, non-overlapping 2theta windows for a broken-axis stack."""
    if not value:
        return [x_limits]
    windows: list[tuple[float, float]] = []
    try:
        for raw_window in value.split(";"):
            lower_text, upper_text = raw_window.split(",", maxsplit=1)
            lower, upper = float(lower_text), float(upper_text)
            windows.append((lower, upper))
    except ValueError as exc:
        raise SystemExit(
            "--x-windows must use 'lower,upper;lower,upper' syntax"
        ) from exc
    if not 1 <= len(windows) <= 3:
        raise SystemExit("--x-windows accepts one to three windows")
    common_lower, common_upper = x_limits
    previous_upper: float | None = None
    for lower, upper in windows:
        if not (math.isfinite(lower) and math.isfinite(upper)) or upper <= lower:
            raise SystemExit("Every --x-windows interval must be finite and increasing")
        if lower < common_lower - 1e-9 or upper > common_upper + 1e-9:
            raise SystemExit(
                f"Requested window [{lower}, {upper}] is outside the common range "
                f"[{common_lower}, {common_upper}]"
            )
        if previous_upper is not None and lower <= previous_upper:
            raise SystemExit("--x-windows intervals must be ordered and non-overlapping")
        previous_upper = upper
    return windows


def representative_indices(frame_count: int, frame_step: int) -> list[int]:
    """Select uniform representative frames while always retaining the endpoint."""
    indices = list(range(0, frame_count, frame_step))
    if indices[-1] != frame_count - 1:
        final_gap = frame_count - 1 - indices[-1]
        if final_gap < max(2, frame_step // 2):
            indices[-1] = frame_count - 1
        else:
            indices.append(frame_count - 1)
    return indices


def add_axis_break_marks(left_ax: plt.Axes, right_ax: plt.Axes) -> None:
    """Draw conventional diagonal marks between adjacent broken x axes."""
    marker_size = 0.012
    left_kwargs = {
        "transform": left_ax.transAxes,
        "color": "black",
        "clip_on": False,
        "lw": 0.85,
    }
    right_kwargs = {
        "transform": right_ax.transAxes,
        "color": "black",
        "clip_on": False,
        "lw": 0.85,
    }
    left_ax.plot(
        (1 - marker_size, 1 + marker_size),
        (-marker_size, marker_size),
        **left_kwargs,
    )
    left_ax.plot(
        (1 - marker_size, 1 + marker_size),
        (1 - marker_size, 1 + marker_size),
        **left_kwargs,
    )
    right_ax.plot(
        (-marker_size, marker_size),
        (-marker_size, marker_size),
        **right_kwargs,
    )
    right_ax.plot(
        (-marker_size, marker_size),
        (1 - marker_size, 1 + marker_size),
        **right_kwargs,
    )


def plot_voltage_panel(
    voltage_ax: plt.Axes,
    voltage: np.ndarray,
    time_h: np.ndarray,
    states: np.ndarray,
    voltage_major_step: float,
    panel_label_outside: bool = False,
    show_panel_label: bool = True,
) -> list[Line2D]:
    """Draw the complete synchronized voltage history with fixed major ticks."""
    voltage_ax.plot(voltage, time_h, color="#9a9a9a", lw=0.65, zorder=1)
    handles: list[Line2D] = []
    for state, color, label in (
        (1, CHARGE_COLOR, "Charge"),
        (-1, DISCHARGE_COLOR, "Discharge"),
        (0, HOLD_COLOR, "Hold/rest"),
    ):
        mask = states == state
        if np.any(mask):
            voltage_ax.scatter(
                voltage[mask],
                time_h[mask],
                s=5.0,
                c=color,
                edgecolors="none",
                zorder=2,
            )
            handles.append(
                Line2D(
                    [0],
                    [0],
                    marker="o",
                    linestyle="none",
                    markerfacecolor=color,
                    markeredgecolor="none",
                    markersize=4.0,
                    label=label,
                )
            )
    voltage_range = float(np.ptp(voltage))
    voltage_padding = max(voltage_major_step * 0.06, voltage_range * 0.02)
    voltage_ax.set_xlim(
        float(np.min(voltage) - voltage_padding),
        float(np.max(voltage) + voltage_padding),
    )
    voltage_ax.xaxis.set_major_locator(mpl.ticker.MultipleLocator(voltage_major_step))
    voltage_ax.xaxis.set_minor_locator(
        mpl.ticker.MultipleLocator(voltage_major_step / 2.0)
    )
    voltage_ax.set_xlabel("Voltage (V)")
    voltage_ax.tick_params(labelleft=False)
    style_axes(voltage_ax)
    voltage_ax.yaxis.set_minor_locator(mpl.ticker.NullLocator())
    if show_panel_label:
        voltage_ax.text(
            0.0 if panel_label_outside else 0.08,
            1.008 if panel_label_outside else 0.985,
            "(b)",
            transform=voltage_ax.transAxes,
            ha="left",
            va="bottom" if panel_label_outside else "top",
            fontsize=10.5,
            fontweight="bold",
            clip_on=False,
        )
    return handles


def make_figure(
    grid: np.ndarray,
    display: np.ndarray,
    frames: list[dict[str, Any]],
    x_limits: tuple[float, float],
    intensity_record: dict[str, Any],
    title: str,
    voltage_major_step: float,
    promotional_layout: bool,
    clean_figure: bool,
) -> tuple[plt.Figure, dict[str, Any]]:
    configure_style()
    time_h = np.asarray([frame["time_s"] for frame in frames], dtype=float) / 3600.0
    voltage = np.asarray([frame["voltage_V"] for frame in frames], dtype=float)
    current = np.asarray([frame["current_mA"] for frame in frames], dtype=float)
    states, current_threshold = electrochemical_segments(current)
    if promotional_layout:
        fig = plt.figure(figsize=(6.35, 4.70), dpi=180)
        grid_spec = fig.add_gridspec(
            2,
            3,
            height_ratios=[0.055, 1.0],
            width_ratios=[4.35, 1.12, 0.92],
            hspace=0.18,
            wspace=0.075,
            left=0.105,
            right=0.985,
            bottom=0.12 if clean_figure else 0.165,
            top=0.94 if clean_figure else 0.84,
        )
        colorbar_ax = fig.add_subplot(grid_spec[0, 0])
        heatmap_ax = fig.add_subplot(grid_spec[1, 0])
        voltage_ax = fig.add_subplot(grid_spec[1, 1], sharey=heatmap_ax)
        legend_ax = fig.add_subplot(grid_spec[1, 2])
        legend_ax.axis("off")
    else:
        fig = plt.figure(figsize=(5.45, 4.25), dpi=180)
        axes = fig.subplots(
            1,
            2,
            sharey=True,
            gridspec_kw={"width_ratios": [4.35, 1.05], "wspace": 0.055},
        )
        heatmap_ax, voltage_ax = axes
        colorbar_ax = None
        legend_ax = None
    mesh = heatmap_ax.pcolormesh(
        grid,
        time_h,
        display,
        shading="auto",
        cmap="viridis",
        vmin=intensity_record["vmin"],
        vmax=intensity_record["vmax"],
        rasterized=True,
    )
    heatmap_ax.set_xlim(*x_limits)
    heatmap_ax.set_xlabel(r"2$\theta$ (Degree)")
    heatmap_ax.set_ylabel("Time (h)")
    style_axes(heatmap_ax)
    if not clean_figure:
        heatmap_ax.text(
            -0.06 if promotional_layout else 0.015,
            1.012 if promotional_layout else 0.985,
            "(a)",
            transform=heatmap_ax.transAxes,
            ha="left",
            va="bottom" if promotional_layout else "top",
            fontsize=10.5,
            fontweight="bold",
            color="black" if promotional_layout else "white",
            clip_on=False,
        )
    handles = plot_voltage_panel(
        voltage_ax,
        voltage,
        time_h,
        states,
        voltage_major_step,
        panel_label_outside=promotional_layout,
        show_panel_label=not clean_figure,
    )
    if promotional_layout and legend_ax is not None:
        legend_ax.legend(
            handles=handles,
            loc="center left",
            bbox_to_anchor=(0.0, 0.5),
            fontsize=6.3,
            frameon=False,
            handletextpad=0.25,
            borderaxespad=0.0,
            labelspacing=0.25,
            markerscale=1.2,
        )
    else:
        voltage_ax.legend(
            handles=handles,
            loc="lower right",
            bbox_to_anchor=(1.0, 0.005),
            fontsize=5.7,
            frameon=False,
            handletextpad=0.25,
            borderaxespad=0.0,
            labelspacing=0.25,
            markerscale=1.2,
        )
    if promotional_layout and colorbar_ax is not None:
        colorbar = fig.colorbar(mesh, cax=colorbar_ax, orientation="horizontal")
        colorbar.ax.xaxis.set_ticks_position("bottom")
        colorbar.ax.xaxis.set_label_position("bottom")
        colorbar.ax.set_title(
            "Relative intensity",
            fontsize=AXIS_LABEL_SIZE,
            pad=3.0,
        )
    else:
        colorbar = fig.colorbar(
            mesh,
            ax=heatmap_ax,
            orientation="horizontal",
            location="top",
            pad=0.025,
            fraction=0.055,
            aspect=32,
        )
        colorbar.set_label("Relative intensity", fontsize=AXIS_LABEL_SIZE, labelpad=2.0)
    colorbar.ax.tick_params(labelsize=TICK_LABEL_SIZE, width=0.7, length=2.5, pad=1.5)
    if not clean_figure:
        fig.suptitle(
            title,
            y=0.975 if promotional_layout else 0.995,
            fontsize=8.8 if promotional_layout else 8.4,
            fontweight="normal",
        )
    if promotional_layout and not clean_figure:
        transform_name = intensity_record["mode"].replace("-", " ")
        fig.text(
            0.5,
            0.031,
            f"Experimental operando XRD | all {len(frames)} frames | {transform_name} intensity display",
            ha="center",
            va="bottom",
            fontsize=ANNOTATION_SIZE,
            color="#333333",
        )
        fig.text(
            0.5,
            0.013,
            "Unsmoothed experimental patterns; not per-frame Rietveld refinement",
            ha="center",
            va="bottom",
            fontsize=ANNOTATION_SIZE - 0.3,
            color="#555555",
        )
    elif not promotional_layout:
        fig.text(
            0.012,
            0.012,
            "Experimental operando XRD (not per-frame Rietveld refinement)",
            ha="left",
            va="bottom",
            fontsize=ANNOTATION_SIZE,
            color="#333333",
        )
        fig.subplots_adjust(left=0.125, right=0.985, bottom=0.14, top=0.88)
    return fig, {
        "time_h": [float(np.min(time_h)), float(np.max(time_h))],
        "voltage_V": [float(np.min(voltage)), float(np.max(voltage))],
        "current_state_threshold_mA": current_threshold,
        "charge_frames": int(np.count_nonzero(states == 1)),
        "discharge_frames": int(np.count_nonzero(states == -1)),
        "hold_or_rest_frames": int(np.count_nonzero(states == 0)),
        "voltage_major_tick_step_V": voltage_major_step,
        "voltage_trajectory_frame_count": len(frames),
        "promotional_layout": promotional_layout,
        "clean_figure": clean_figure,
        "visible_title_panel_labels_and_footer": not clean_figure,
        "text_data_overlap_prevention": (
            "dedicated colorbar row and legend column; panel labels above axes; footer below axes"
            if promotional_layout
            else "standard layout"
        ),
    }


def make_stacked_figure(
    grid: np.ndarray,
    display: np.ndarray,
    frames: list[dict[str, Any]],
    x_limits: tuple[float, float],
    intensity_record: dict[str, Any],
    title: str,
    stack_height_h: float,
    x_windows: list[tuple[float, float]],
    frame_step: int,
    voltage_major_step: float,
    allow_profile_overlap: bool,
    peak_gamma: float,
    promotional_layout: bool,
    clean_figure: bool,
) -> tuple[plt.Figure, dict[str, Any]]:
    """Draw audited experimental frames as a disclosed representative stack."""
    configure_style()
    time_h = np.asarray([frame["time_s"] for frame in frames], dtype=float) / 3600.0
    voltage = np.asarray([frame["voltage_V"] for frame in frames], dtype=float)
    current = np.asarray([frame["current_mA"] for frame in frames], dtype=float)
    states, current_threshold = electrochemical_segments(current)
    selected_indices = representative_indices(len(frames), frame_step)
    selected_time_h = time_h[selected_indices]
    minimum_selected_gap_h = float(np.min(np.diff(selected_time_h)))
    effective_height_h = (
        stack_height_h
        if allow_profile_overlap
        else min(stack_height_h, minimum_selected_gap_h * 0.72)
    )
    baseline = np.percentile(display, 2.0, axis=1, keepdims=True)
    shifted = np.clip(display - baseline, 0.0, None)
    reference = float(np.max(shifted[selected_indices]))
    if reference <= 0:
        raise SystemExit("Stacked profiles have no positive intensity span")
    normalized_profiles = np.clip(shifted / reference, 0.0, None)
    stacked = np.power(normalized_profiles, peak_gamma) * effective_height_h

    figure_size = (6.35, 4.70) if promotional_layout else (6.15, 4.55)
    fig = plt.figure(figsize=figure_size, dpi=180)
    window_widths = [upper - lower for lower, upper in x_windows]
    width_scale = min(window_widths)
    grid_spec = fig.add_gridspec(
        1,
        len(x_windows) + 2,
        width_ratios=[width / width_scale for width in window_widths] + [1.15, 0.92],
        wspace=0.075,
    )
    stack_axes: list[plt.Axes] = []
    for position in range(len(x_windows)):
        share_axis = stack_axes[0] if stack_axes else None
        stack_axes.append(fig.add_subplot(grid_spec[0, position], sharey=share_axis))
    voltage_ax = fig.add_subplot(grid_spec[0, len(x_windows)], sharey=stack_axes[0])
    legend_ax = fig.add_subplot(grid_spec[0, len(x_windows) + 1])
    legend_ax.axis("off")
    colors = {1: CHARGE_COLOR, -1: DISCHARGE_COLOR, 0: HOLD_COLOR}
    maximum_profile_y = max(
        float(time_h[index] + np.max(stacked[index])) for index in selected_indices
    )
    top_padding_h = max(0.10, minimum_selected_gap_h * 0.18)
    for stack_ax, window in zip(stack_axes, x_windows):
        window_mask = (grid >= window[0]) & (grid <= window[1])
        for index in selected_indices:
            stack_ax.plot(
                grid[window_mask],
                time_h[index] + stacked[index, window_mask],
                color=colors[int(states[index])],
                lw=0.42,
                alpha=0.9,
                solid_capstyle="round",
                rasterized=True,
            )
        stack_ax.set_xlim(*window)
        stack_ax.set_ylim(
            float(np.min(selected_time_h)),
            maximum_profile_y + top_padding_h,
        )
        style_axes(stack_ax)
    stack_axes[0].set_ylabel("Time (h)")
    if not clean_figure:
        stack_axes[0].text(
            0.0 if promotional_layout else 0.015,
            1.008 if promotional_layout else 0.985,
            "(a)",
            transform=stack_axes[0].transAxes,
            ha="left",
            va="bottom" if promotional_layout else "top",
            fontsize=10.5,
            fontweight="bold",
            clip_on=False,
        )
    for hidden_y_axis in stack_axes[1:]:
        hidden_y_axis.tick_params(left=False, labelleft=False)
        hidden_y_axis.yaxis.set_minor_locator(mpl.ticker.NullLocator())
    for left_ax, right_ax in zip(stack_axes[:-1], stack_axes[1:]):
        left_ax.spines["right"].set_visible(False)
        right_ax.spines["left"].set_visible(False)
        add_axis_break_marks(left_ax, right_ax)

    handles = plot_voltage_panel(
        voltage_ax,
        voltage,
        time_h,
        states,
        voltage_major_step,
        panel_label_outside=promotional_layout,
        show_panel_label=not clean_figure,
    )
    legend_ax.legend(
        handles=handles,
        loc="center left",
        bbox_to_anchor=(0.0, 0.5),
        fontsize=6.3 if promotional_layout else 5.7,
        frameon=False,
        handletextpad=0.25,
        borderaxespad=0.0,
        labelspacing=0.25,
        markerscale=1.2,
    )
    if not clean_figure:
        fig.suptitle(
            title,
            y=0.982 if promotional_layout else 0.995,
            fontsize=8.8 if promotional_layout else 8.4,
            fontweight="normal",
        )
    mode_text = intensity_record["mode"].replace("-", " ")
    sampling_text = (
        "all frames" if frame_step == 1 else f"representative profiles every {frame_step}th frame"
    )
    overlap_text = "; profile overlap allowed" if allow_profile_overlap else ""
    gamma_text = f"; peak-display gamma {peak_gamma:.2f}" if peak_gamma < 1.0 else ""
    if promotional_layout and not clean_figure:
        fig.text(
            0.5,
            0.031,
            rf"Experimental operando XRD | every {frame_step}th frame | weak-peak display $\gamma$ = {peak_gamma:.2f}",
            ha="center",
            va="bottom",
            fontsize=ANNOTATION_SIZE,
            color="#333333",
        )
        fig.text(
            0.5,
            0.013,
            "Display-enhanced experimental patterns; not per-frame Rietveld refinement",
            ha="center",
            va="bottom",
            fontsize=ANNOTATION_SIZE - 0.3,
            color="#555555",
        )
        fig.subplots_adjust(left=0.105, right=0.985, bottom=0.165, top=0.895)
    elif not promotional_layout:
        fig.text(
            0.012,
            0.012,
            f"Experimental operando XRD; {sampling_text}; {mode_text} display{gamma_text}{overlap_text}; not per-frame Rietveld refinement",
            ha="left",
            va="bottom",
            fontsize=ANNOTATION_SIZE,
            color="#333333",
        )
        fig.subplots_adjust(left=0.105, right=0.985, bottom=0.145, top=0.955)
    else:
        fig.subplots_adjust(left=0.105, right=0.985, bottom=0.13, top=0.96)
    xrd_left = stack_axes[0].get_position().x0
    xrd_right = stack_axes[-1].get_position().x1
    fig.text(
        (xrd_left + xrd_right) / 2.0,
        0.055 if clean_figure else (0.086 if promotional_layout else 0.072),
        r"2$\theta$ (Degree)",
        ha="center",
        va="center",
        fontsize=AXIS_LABEL_SIZE,
    )
    return fig, {
        "time_h": [float(np.min(time_h)), float(np.max(time_h))],
        "voltage_V": [float(np.min(voltage)), float(np.max(voltage))],
        "current_state_threshold_mA": current_threshold,
        "charge_frames": int(np.count_nonzero(states == 1)),
        "discharge_frames": int(np.count_nonzero(states == -1)),
        "hold_or_rest_frames": int(np.count_nonzero(states == 0)),
        "vertical_coordinate": "measured time_h plus display-only intensity offset",
        "stack_height_h_requested": stack_height_h,
        "stack_height_h_effective_maximum": effective_height_h,
        "minimum_selected_time_gap_h": minimum_selected_gap_h,
        "profile_overlap_allowed": allow_profile_overlap,
        "no_profile_overlap_by_vertical_extent": effective_height_h < minimum_selected_gap_h,
        "peak_display_gamma": peak_gamma,
        "peak_display_transform": (
            "normalized positive profile intensity raised to gamma; display only"
        ),
        "top_padding_h": top_padding_h,
        "maximum_profile_y_h": maximum_profile_y,
        "frame_step": frame_step,
        "frame_count_drawn": len(selected_indices),
        "selected_frame_indices": selected_indices,
        "selected_frame_ids": [str(frames[index]["frame_id"]) for index in selected_indices],
        "x_windows": [list(window) for window in x_windows],
        "voltage_major_tick_step_V": voltage_major_step,
        "voltage_trajectory_frame_count": len(frames),
        "profile_line_width_pt": 0.42,
        "promotional_layout": promotional_layout,
        "clean_figure": clean_figure,
        "visible_title_panel_labels_and_footer": not clean_figure,
        "text_data_overlap_prevention": (
            "panel labels above axes; legend in dedicated axis; footer below axes"
            if promotional_layout
            else "standard layout"
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Synchronized operando manifest CSV")
    parser.add_argument("--conversion-audit", required=True, help="STOE conversion audit JSON")
    parser.add_argument("--sync-audit", required=True, help="Manifest synchronization audit JSON")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--title", help="Figure title; defaults to sample id")
    parser.add_argument(
        "--view",
        choices=("heatmap", "stacked"),
        default="heatmap",
        help="Experimental operando view; every frame is retained in either mode",
    )
    parser.add_argument("--x-min", type=float)
    parser.add_argument("--x-max", type=float)
    parser.add_argument(
        "--intensity-mode",
        choices=("log", "sqrt", "raw", "per-frame"),
        default="log",
    )
    parser.add_argument("--clip-percentile", type=float, default=99.7)
    parser.add_argument(
        "--stack-height-h",
        type=float,
        default=0.42,
        help="Display height in hours assigned to the global 99.5th-percentile profile amplitude",
    )
    parser.add_argument(
        "--frame-step",
        type=int,
        default=1,
        help="Draw every nth profile in stacked view; all frames remain hash-verified",
    )
    parser.add_argument(
        "--x-windows",
        help="Ordered stacked-view windows, for example '6,8.5;14,21'",
    )
    parser.add_argument(
        "--voltage-major-step",
        type=float,
        default=0.5,
        help="Voltage-axis major tick interval in volts",
    )
    parser.add_argument(
        "--allow-profile-overlap",
        action="store_true",
        help="Allow enlarged peak profiles to cross adjacent time-offset baselines",
    )
    parser.add_argument(
        "--peak-gamma",
        type=float,
        default=1.0,
        help="Display-only peak exponent; values below 1 raise weak peaks without smoothing",
    )
    parser.add_argument(
        "--promotional-layout",
        action="store_true",
        help="Use separated title, panel labels, legend, and two-line disclosure footer",
    )
    parser.add_argument(
        "--clean-figure",
        action="store_true",
        help="With promotional layout, show only data, axes, colorbar, and legend",
    )
    parser.add_argument("--formats", default="png,svg")
    parser.add_argument(
        "--skip-raw-source-hash-check",
        action="store_true",
        help="Verify converted patterns but skip large raw-source rehashing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 90.0 <= args.clip_percentile <= 100.0:
        raise SystemExit("--clip-percentile must be between 90 and 100")
    if args.stack_height_h <= 0:
        raise SystemExit("--stack-height-h must be positive")
    if args.frame_step <= 0:
        raise SystemExit("--frame-step must be a positive integer")
    if args.voltage_major_step <= 0:
        raise SystemExit("--voltage-major-step must be positive")
    if not 0 < args.peak_gamma <= 1:
        raise SystemExit("--peak-gamma must be greater than 0 and no greater than 1")
    manifest_path = Path(args.manifest).expanduser().resolve()
    conversion_path = Path(args.conversion_audit).expanduser().resolve()
    sync_path = Path(args.sync_audit).expanduser().resolve()
    for path, label in (
        (manifest_path, "manifest"),
        (conversion_path, "conversion audit"),
        (sync_path, "synchronization audit"),
    ):
        if not path.is_file():
            raise SystemExit(f"{label} not found: {path}")
    source_hashes_before = {
        "manifest": sha256_file(manifest_path),
        "conversion_audit": sha256_file(conversion_path),
        "sync_audit": sha256_file(sync_path),
    }
    conversion = load_json(conversion_path, "conversion audit")
    sync = load_json(sync_path, "synchronization audit")
    conversions, verified_files = verified_conversion_frames(
        conversion, not args.skip_raw_source_hash_check
    )
    rows = read_manifest(manifest_path)
    validate_sync_binding(sync, manifest_path, source_hashes_before["manifest"], rows)
    frames = bind_and_read_patterns(rows, conversions)
    grid, matrix, x_limits, interpolation = common_matrix(
        frames, args.x_min, args.x_max
    )
    display, intensity_record = transform_intensity(
        matrix, args.intensity_mode, args.clip_percentile
    )
    x_windows = parse_x_windows(args.x_windows, x_limits)
    if args.view != "stacked" and args.x_windows:
        raise SystemExit("--x-windows is available only with --view stacked")
    if args.clean_figure and not args.promotional_layout:
        raise SystemExit("--clean-figure requires --promotional-layout")
    if args.view == "stacked":
        figure, electrochemistry = make_stacked_figure(
            grid,
            display,
            frames,
            x_limits,
            intensity_record,
            args.title or args.sample_id,
            args.stack_height_h,
            x_windows,
            args.frame_step,
            args.voltage_major_step,
            args.allow_profile_overlap,
            args.peak_gamma,
            args.promotional_layout,
            args.clean_figure,
        )
    else:
        figure, electrochemistry = make_figure(
            grid,
            display,
            frames,
            x_limits,
            intensity_record,
            args.title or args.sample_id,
            args.voltage_major_step,
            args.promotional_layout,
            args.clean_figure,
        )
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sample_id = clean_name(args.sample_id)
    formats = [item.strip().lower().lstrip(".") for item in args.formats.split(",") if item.strip()]
    if not formats:
        raise SystemExit("At least one output format is required")
    outputs: dict[str, str] = {}
    for suffix in formats:
        if suffix not in {"png", "svg", "pdf", "tif", "tiff"}:
            raise SystemExit(f"Unsupported output format: {suffix}")
        view_suffix = "_stacked" if args.view == "stacked" else ""
        if args.view == "stacked" and args.x_windows:
            view_suffix += "_broken_axis"
        if args.view == "stacked" and args.allow_profile_overlap:
            view_suffix += "_peak_emphasis"
        if args.view == "stacked" and args.peak_gamma < 1.0:
            view_suffix += "_weak_peaks"
        if args.promotional_layout:
            view_suffix += "_promo"
        if args.clean_figure:
            view_suffix += "_clean"
        output = out_dir / f"{sample_id}_experimental_operando{view_suffix}.{suffix}"
        kwargs = {"dpi": OUTPUT_DPI} if suffix in {"png", "tif", "tiff"} else {}
        figure.savefig(output, bbox_inches="tight", pad_inches=0.035, **kwargs)
        outputs[suffix] = str(output)
    plt.close(figure)
    source_hashes_after = {
        "manifest": sha256_file(manifest_path),
        "conversion_audit": sha256_file(conversion_path),
        "sync_audit": sha256_file(sync_path),
    }
    if source_hashes_after != source_hashes_before:
        raise SystemExit("Input audit or manifest changed during plotting")
    for record in verified_files:
        if sha256_file(Path(record["path"])) != record["sha256"]:
            raise SystemExit(f"Input changed during plotting: {record['path']}")
    manifest = {
        "schema_version": 1,
        "style_profile": STYLE_PROFILE,
        "figure_claim": "experimental operando XRD with synchronized electrochemistry",
        "per_frame_rietveld_claimed": False,
        "view": args.view,
        "sample_id": sample_id,
        "frame_count": len(frames),
        "sources": {
            "manifest": {"path": str(manifest_path), "sha256": source_hashes_before["manifest"]},
            "conversion_audit": {"path": str(conversion_path), "sha256": source_hashes_before["conversion_audit"]},
            "sync_audit": {"path": str(sync_path), "sha256": source_hashes_before["sync_audit"]},
            "verified_raw_sources": not args.skip_raw_source_hash_check,
            "verified_file_count": len(verified_files),
        },
        "synchronization": {
            "mode": sync.get("mode"),
            "interpolation_performed": False,
            "maximum_absolute_sync_delta_s": sync.get("maximum_absolute_sync_delta_s"),
            "maximum_allowed_delta_s": sync.get("maximum_delta_s"),
        },
        "x_range": list(x_limits),
        "x_windows": [list(window) for window in x_windows],
        "common_grid_points": int(grid.size),
        "interpolation": interpolation,
        "intensity_display": intensity_record,
        "electrochemistry": electrochemistry,
        "style": {
            "output_dpi": OUTPUT_DPI,
            "white_background": True,
            "boxed_axes": True,
            "grid": False,
        },
        "outputs": outputs,
        "integrity": "all recorded hashes identical before and after plotting",
    }
    manifest_view_suffix = "_stacked" if args.view == "stacked" else ""
    if args.view == "stacked" and args.x_windows:
        manifest_view_suffix += "_broken_axis"
    if args.view == "stacked" and args.allow_profile_overlap:
        manifest_view_suffix += "_peak_emphasis"
    if args.view == "stacked" and args.peak_gamma < 1.0:
        manifest_view_suffix += "_weak_peaks"
    if args.promotional_layout:
        manifest_view_suffix += "_promo"
    if args.clean_figure:
        manifest_view_suffix += "_clean"
    output_manifest = out_dir / (
        f"{sample_id}_experimental_operando{manifest_view_suffix}_plot_manifest.json"
    )
    output_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"outputs": outputs, "manifest": str(output_manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
