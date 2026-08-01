#!/usr/bin/env python3
"""Create reference-inspired Rietveld plots read-only from a final GSAS-II GPX."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import AutoMinorLocator, MultipleLocator
import numpy as np


DEFAULT_GSASII = Path(
    os.environ.get("GSASII_DIR", Path.home() / "g2main" / "GSAS-II")
)
DEFAULT_FORMATS = ("png",)
STYLE_PROFILE = "locked-reference-v1"
DEFAULT_X_MIN = 10.0
DEFAULT_X_MAX = 60.0
DEFAULT_FIGURE_WIDTH = 4.05
DEFAULT_FIGURE_HEIGHT = 3.35
DEFAULT_MARKER_STEP = 1
OUTPUT_DPI = 600
EXPERIMENTAL_MARKER_SIZE = 2.05
EXPERIMENTAL_COLOR = "#d62728"
CALCULATION_COLOR = "black"
DIFFERENCE_COLOR = "#1f4ed8"
BRAGG_COLOR = "#1b9e77"
CALCULATION_LINE_WIDTH = 0.48
DIFFERENCE_LINE_WIDTH = 0.50
BRAGG_LINE_WIDTH = 0.45
LEGEND_FONT_SIZE = 5.8
FIT_STATISTICS_FONT_SIZE = 5.8
FIT_STATISTICS_LABEL_X = 0.835
FIT_STATISTICS_EQUALS_X = 0.895
FIT_STATISTICS_VALUE_X = 0.915
FIT_STATISTICS_TOP_Y = 0.735
FIT_STATISTICS_ROW_STEP = 0.044


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_gsasii(gsasii_dir: Path):
    sys.path.insert(0, str(gsasii_dir))
    from GSASII import GSASIIscriptable as G2sc  # type: ignore

    return G2sc


def clean_name(value: str) -> str:
    keep = []
    for ch in value.strip():
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        elif ch in " /\\:;,+()[]{}":
            keep.append("_")
    cleaned = "".join(keep).strip("._-")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned or "sample"


def hkl_label(hkl: tuple[int, int, int]) -> str:
    if all(0 <= v <= 9 for v in hkl):
        return f"({hkl[0]}{hkl[1]}{hkl[2]})"
    return f"({hkl[0]},{hkl[1]},{hkl[2]})"


def configure_style() -> None:
    available_fonts = {font.name for font in mpl.font_manager.fontManager.ttflist}
    base_font = "Arial" if "Arial" in available_fonts else "DejaVu Sans"
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [base_font],
            "font.size": 6,
            "mathtext.fontset": "custom",
            "mathtext.rm": base_font,
            "mathtext.it": base_font,
            "mathtext.bf": base_font,
            "mathtext.sf": base_font,
            "axes.labelsize": 8.5,
            "axes.linewidth": 1.0,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.major.width": 1.0,
            "ytick.major.width": 1.0,
            "xtick.minor.width": 0.8,
            "ytick.minor.width": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
        }
    )


def get_histogram(gpx, histogram_index: int):
    histograms = gpx.histograms()
    powder = [hist for hist in histograms if "PWDR" in hist.name.upper() or hasattr(hist, "data")]
    if not powder:
        raise SystemExit("No powder histogram found in GPX file")
    if histogram_index < 0 or histogram_index >= len(powder):
        raise SystemExit(f"Histogram index {histogram_index} is out of range; found {len(powder)} powder histograms")
    return powder[histogram_index]


def extract_profile(hist) -> dict[str, np.ndarray]:
    arr = np.asarray(hist.data["data"][1], dtype=float)
    if arr.shape[0] < 6:
        raise SystemExit("Unexpected GSAS-II histogram data shape; need x, obs, calc, background, difference arrays")
    return {
        "TwoTheta": arr[0],
        "Observed": arr[1],
        "CalculatedLine": arr[3],
        "Background": arr[4],
        "Difference": arr[5],
    }


def extract_fit_statistics(gpx, hist) -> dict[str, float | None]:
    """Read accurately named whole-pattern statistics without changing the GPX."""
    rvals = gpx["Covariance"]["data"].get("Rvals", {})
    residuals = hist.residuals
    return {
        "Rwp": (
            float(rvals["Rwp"])
            if rvals.get("Rwp") is not None
            else (
                float(residuals["wR"])
                if residuals.get("wR") is not None
                else None
            )
        ),
        "Rp": (
            float(residuals["R"])
            if residuals.get("R") is not None
            else None
        ),
        "GOF": (
            float(rvals["GOF"])
            if rvals.get("GOF") is not None
            else None
        ),
    }


def extract_reflections(hist, x_min: float, x_max: float) -> list[dict[str, object]]:
    reflections: list[dict[str, object]] = []
    for phase_name, refl_data in hist.data.get("Reflection Lists", {}).items():
        for row in refl_data.get("RefList", []):
            two_theta = float(row[5])
            if x_min <= two_theta <= x_max:
                hkl = tuple(int(round(v)) for v in row[:3])
                intensity = float(row[8])
                reflections.append(
                    {
                        "phase": str(phase_name),
                        "two_theta": two_theta,
                        "intensity": intensity,
                        "hkl": hkl,
                        "label": hkl_label(hkl),
                    }
                )
    if not reflections:
        raise SystemExit("No reflections found in the requested x range")
    return reflections


def select_peak_labels(
    reflections: list[dict[str, object]],
    max_labels: int,
    min_separation: float,
) -> list[dict[str, object]]:
    ranked = sorted(reflections, key=lambda row: float(row["intensity"]), reverse=True)
    selected: list[dict[str, object]] = []
    for row in ranked:
        two_theta = float(row["two_theta"])
        if any(abs(two_theta - float(old["two_theta"])) < min_separation for old in selected):
            continue
        selected.append(row)
        if len(selected) >= max_labels:
            break
    return sorted(selected, key=lambda row: float(row["two_theta"]))


def local_profile_peak_y(plot_data: dict[str, np.ndarray], two_theta: float, x_window: float) -> float:
    x = plot_data["TwoTheta"]
    calculated = plot_data["DisplayCalculated"]
    observed = plot_data["DisplayObserved"]
    near_peak = np.abs(x - two_theta) <= x_window
    if not np.any(near_peak):
        idx = int(np.nanargmin(np.abs(x - two_theta)))
        return float(calculated[idx])
    calc_y = float(np.nanmax(calculated[near_peak]))
    obs_y = float(np.nanmax(observed[near_peak]))
    return max(calc_y, obs_y)


def label_positions(
    plot_data: dict[str, np.ndarray],
    labels: list[dict[str, object]],
    x_min: float,
    x_max: float,
) -> list[dict[str, object]]:
    """Place HKL labels at true Bragg x positions with vertical clearance.

    Earlier versions shifted the strongest peak label sideways to avoid clipping.
    That made the label look like it belonged to the wrong Bragg position. Here
    x remains the GSAS-II reflection position; only the label height is adjusted.
    """
    y_min = float(plot_data["YMin"][0])
    y_max = float(plot_data["YMax"][0])
    y_range = y_max - y_min
    x_visible = (plot_data["TwoTheta"] >= x_min) & (plot_data["TwoTheta"] <= x_max)
    data_floor = float(np.nanmin(plot_data["DisplayObserved"][x_visible]))
    data_ceiling = float(np.nanmax(plot_data["DisplayObserved"][x_visible]))
    data_span = data_ceiling - data_floor
    x_range = x_max - x_min
    peak_window = max(0.10, 0.0035 * x_range)
    base_offset = 0.018 * y_range
    lane_step = 0.034 * y_range
    label_height_guard = 0.105 * y_range
    label_top = y_max - label_height_guard
    label_bottom = data_floor + 0.18 * data_span

    placed: list[dict[str, object]] = []
    occupied: list[tuple[float, float, float]] = []
    for item in labels:
        two_theta = float(item["two_theta"])
        if not x_min <= two_theta <= x_max:
            continue
        text_x = two_theta
        peak_y = local_profile_peak_y(plot_data, two_theta, peak_window)

        text_y = peak_y + base_offset
        for lane in range(5):
            candidate_y = peak_y + base_offset + lane * lane_step
            too_close = any(abs(text_x - old_x) < 0.42 and abs(candidate_y - old_y) < 0.11 * y_range for old_x, old_y, _ in occupied)
            if not too_close:
                text_y = candidate_y
                break
        text_y = min(max(text_y, label_bottom), label_top)
        occupied.append((text_x, text_y, peak_y))
        placed.append({**item, "label_x": text_x, "label_y": text_y, "peak_y": peak_y})
    return placed


def prepare_plot_data(
    profile: dict[str, np.ndarray],
    reflections: list[dict[str, object]],
    x_min: float,
    x_max: float,
    marker_step: int,
    subtract_background: bool,
) -> dict[str, np.ndarray]:
    x = profile["TwoTheta"]
    visible = (x >= x_min) & (x <= x_max)
    if not np.any(visible):
        raise SystemExit("Requested x range does not overlap histogram data")

    if subtract_background:
        display_observed = profile["Observed"] - profile["Background"]
        display_calculated = profile["CalculatedLine"] - profile["Background"]
    else:
        display_observed = profile["Observed"]
        display_calculated = profile["CalculatedLine"]

    y_min = float(np.nanmin(display_observed[visible]))
    y_max = float(np.nanmax(display_observed[visible]))
    span = y_max - y_min
    if span <= 0:
        raise SystemExit("Observed intensity span is zero; cannot scale plot")

    experimental_markers = np.full_like(profile["Observed"], np.nan)
    step = max(1, marker_step)
    sampled_mask = visible & ((np.arange(x.size) % step) == 0)
    experimental_markers[sampled_mask] = display_observed[sampled_mask]

    difference_offset = y_min - 0.24 * span
    tick_base = y_min - 0.14 * span
    tick_top = y_min - 0.09 * span

    bragg_positions = np.asarray(sorted({round(float(row["two_theta"]), 5) for row in reflections}), dtype=float)
    return {
        **profile,
        "DisplayObserved": display_observed,
        "DisplayCalculated": display_calculated,
        "ExperimentalMarkers": experimental_markers,
        "DifferenceOffset": profile["Difference"] + difference_offset,
        "BraggTwoTheta": bragg_positions,
        "BraggY0": np.full_like(bragg_positions, tick_base),
        "BraggY1": np.full_like(bragg_positions, tick_top),
        "YMin": np.asarray([difference_offset - 0.09 * span]),
        "YMax": np.asarray([y_max + 0.24 * span]),
    }


def plot_rietveld(
    plot_data: dict[str, np.ndarray],
    labels: list[dict[str, object]],
    statistics: dict[str, float | None],
    x_min: float,
    x_max: float,
    panel: str | None,
    show_y_values: bool,
    show_hkl_labels: bool,
    show_fit_statistics: bool,
    figure_width: float,
    figure_height: float,
) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(figure_width, figure_height), dpi=200)

    x = plot_data["TwoTheta"]
    y_min = float(plot_data["YMin"][0])
    y_max = float(plot_data["YMax"][0])
    y_range = y_max - y_min

    marker_mask = np.isfinite(plot_data["ExperimentalMarkers"])
    ax.plot(
        x[marker_mask],
        plot_data["ExperimentalMarkers"][marker_mask],
        linestyle="None",
        marker="o",
        markersize=EXPERIMENTAL_MARKER_SIZE,
        markerfacecolor="none",
        markeredgecolor=EXPERIMENTAL_COLOR,
        markeredgewidth=0.38,
        label="Experimental",
        zorder=2,
    )

    ax.plot(
        x,
        plot_data["DisplayCalculated"],
        color=CALCULATION_COLOR,
        lw=CALCULATION_LINE_WIDTH,
        label="Calculation",
        zorder=3,
    )

    ax.plot(
        x,
        plot_data["DifferenceOffset"],
        color=DIFFERENCE_COLOR,
        lw=DIFFERENCE_LINE_WIDTH,
        label="Difference",
        zorder=2,
    )
    ax.vlines(
        plot_data["BraggTwoTheta"],
        plot_data["BraggY0"],
        plot_data["BraggY1"],
        color=BRAGG_COLOR,
        lw=BRAGG_LINE_WIDTH,
        label="Bragg position",
        zorder=1,
    )

    if show_hkl_labels:
        for item in label_positions(plot_data, labels, x_min, x_max):
            text_x = float(item["label_x"])
            text_y = float(item["label_y"])
            peak_y = float(item["peak_y"])
            if text_y - peak_y > 0.035 * y_range:
                ax.plot(
                    [text_x, text_x],
                    [peak_y + 0.006 * y_range, text_y - 0.006 * y_range],
                    color="#777777",
                    lw=0.28,
                    alpha=0.70,
                    zorder=5,
                    solid_capstyle="round",
                )
            ax.text(
                text_x,
                text_y,
                str(item["label"]),
                rotation=90,
                ha="center",
                va="bottom",
                fontsize=5.2,
                color="black",
                clip_on=True,
                zorder=6,
            )

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_xlabel(r"2$\theta$ (Degree)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.xaxis.set_major_locator(MultipleLocator(10))
    ax.xaxis.set_minor_locator(MultipleLocator(5))
    ax.yaxis.set_minor_locator(AutoMinorLocator(2))
    if not show_y_values:
        ax.tick_params(axis="y", labelleft=False)
    ax.tick_params(which="major", length=5, pad=3)
    ax.tick_params(which="minor", length=3)

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1.0)
        spine.set_color("black")

    legend_handles = [
        Line2D(
            [0],
            [0],
            linestyle="None",
            marker="o",
            markersize=EXPERIMENTAL_MARKER_SIZE,
            markerfacecolor="none",
            markeredgecolor=EXPERIMENTAL_COLOR,
            markeredgewidth=0.55,
            color=EXPERIMENTAL_COLOR,
            label="Experimental",
        ),
        Line2D([0], [0], color=CALCULATION_COLOR, lw=1.0, label="Calculation"),
        Line2D([0], [0], color=DIFFERENCE_COLOR, lw=1.0, label="Difference"),
        Line2D([0], [0], linestyle="None", marker="|", markersize=6, markeredgewidth=0.9, color=BRAGG_COLOR, label="Bragg position"),
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper right",
        bbox_to_anchor=(0.985, 0.98),
        frameon=False,
        fontsize=LEGEND_FONT_SIZE,
        handlelength=1.25,
        handletextpad=0.38,
        borderaxespad=0.0,
        labelspacing=0.28,
    )

    if show_fit_statistics:
        statistic_rows: list[tuple[str, str]] = []
        if statistics.get("Rwp") is not None:
            statistic_rows.append(
                (
                    r"$\mathsf{R_{wp}}$",
                    rf"$\mathsf{{{float(statistics['Rwp']):.2f}\%}}$",
                )
            )
        if statistics.get("Rp") is not None:
            statistic_rows.append(
                (
                    r"$\mathsf{R_p}$",
                    rf"$\mathsf{{{float(statistics['Rp']):.2f}\%}}$",
                )
            )
        if statistics.get("GOF") is not None:
            statistic_rows.append(
                (
                    r"$\mathsf{GOF}$",
                    rf"$\mathsf{{{float(statistics['GOF']):.2f}}}$",
                )
            )
        for row_index, (label_text, value_text) in enumerate(statistic_rows):
            y_position = FIT_STATISTICS_TOP_Y - row_index * FIT_STATISTICS_ROW_STEP
            common_style = {
                "transform": ax.transAxes,
                "va": "top",
                "fontsize": FIT_STATISTICS_FONT_SIZE,
                "fontfamily": "sans-serif",
                "fontweight": "normal",
                "color": "black",
                "zorder": 7,
            }
            ax.text(FIT_STATISTICS_LABEL_X, y_position, label_text, ha="left", **common_style)
            ax.text(FIT_STATISTICS_EQUALS_X, y_position, r"$=$", ha="center", **common_style)
            ax.text(FIT_STATISTICS_VALUE_X, y_position, value_text, ha="left", **common_style)

    if panel:
        ax.text(-0.065, 1.035, panel, transform=ax.transAxes, ha="left", va="bottom", fontsize=12, fontweight="bold", clip_on=False)

    fig.subplots_adjust(left=0.13, right=0.975, bottom=0.16, top=0.965)
    return fig


def save_formats(fig: plt.Figure, out_dir: Path, stem: str, formats: list[str]) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for fmt_name in formats:
        suffix = fmt_name.lower().lstrip(".")
        if suffix not in {"png", "tif", "tiff", "pdf", "svg"}:
            raise SystemExit(f"Unsupported output format: {fmt_name}")
        path = out_dir / f"{stem}.{suffix}"
        kwargs = {"dpi": OUTPUT_DPI} if suffix in {"png", "tif", "tiff"} else {}
        fig.savefig(path, bbox_inches="tight", pad_inches=0.03, **kwargs)
        outputs[suffix] = str(path)
    return outputs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpx", required=True, help="Final GSAS-II .gpx project")
    parser.add_argument("--out-dir", required=True, help="Output directory for the final PNG plot")
    parser.add_argument("--sample-id", help="Sample name used for output file stems; defaults to GPX stem")
    parser.add_argument("--stem", help="Exact output stem; defaults to <sample-id>_python_rietveld")
    parser.add_argument("--histogram-index", type=int, default=0)
    parser.add_argument("--gsasii-dir", default=str(DEFAULT_GSASII))
    parser.add_argument("--x-min", type=float, default=DEFAULT_X_MIN)
    parser.add_argument("--x-max", type=float, default=DEFAULT_X_MAX)
    parser.add_argument("--figure-width", type=float, default=DEFAULT_FIGURE_WIDTH, help="Figure width in inches")
    parser.add_argument("--figure-height", type=float, default=DEFAULT_FIGURE_HEIGHT, help="Figure height in inches")
    parser.add_argument(
        "--marker-step",
        type=int,
        default=DEFAULT_MARKER_STEP,
        help="Plot every Nth experimental marker; default 1 shows every measured point",
    )
    parser.add_argument(
        "--include-background",
        action="store_true",
        help="Display observed and calculated intensities with the fitted background included",
    )
    parser.add_argument("--max-labels", type=int, default=8)
    parser.add_argument("--label-separation", type=float, default=1.8)
    parser.add_argument(
        "--show-hkl-labels",
        action="store_true",
        help="Show selected HKL text labels; hidden by default",
    )
    parser.add_argument(
        "--hide-fit-statistics",
        action="store_true",
        help="Hide the Rwp, Rp, and GOF block",
    )
    parser.add_argument("--panel", help="Optional panel letter such as a or b")
    parser.add_argument("--show-y-values", action="store_true")
    parser.add_argument("--formats", default=",".join(DEFAULT_FORMATS), help="Comma-separated formats; keep PNG only by default")
    parser.add_argument("--write-plot-manifest", action="store_true", help="Debug option: write a plot manifest JSON beside the image")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.x_max <= args.x_min:
        raise SystemExit("--x-max must be greater than --x-min")
    if args.figure_width <= 0 or args.figure_height <= 0:
        raise SystemExit("--figure-width and --figure-height must be positive")
    if args.marker_step < 1:
        raise SystemExit("--marker-step must be an integer of 1 or greater")

    gpx_path = Path(args.gpx).expanduser().resolve()
    if not gpx_path.is_file():
        raise SystemExit(f"GPX file not found: {gpx_path}")
    gpx_hash_before = sha256_file(gpx_path)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sample_id = clean_name(args.sample_id or gpx_path.stem)
    stem = clean_name(args.stem or f"{sample_id}_python_rietveld")
    formats = [item.strip().lower() for item in args.formats.split(",") if item.strip()]

    G2sc = load_gsasii(Path(args.gsasii_dir).expanduser().resolve())
    gpx = G2sc.G2Project(str(gpx_path))
    hist = get_histogram(gpx, args.histogram_index)

    configure_style()
    profile = extract_profile(hist)
    statistics = extract_fit_statistics(gpx, hist)
    reflections = extract_reflections(hist, args.x_min, args.x_max)
    labels = select_peak_labels(reflections, args.max_labels, args.label_separation)
    plot_data = prepare_plot_data(
        profile,
        reflections,
        args.x_min,
        args.x_max,
        args.marker_step,
        not args.include_background,
    )

    fig = plot_rietveld(
        plot_data,
        labels,
        statistics,
        args.x_min,
        args.x_max,
        args.panel,
        args.show_y_values,
        args.show_hkl_labels,
        not args.hide_fit_statistics,
        args.figure_width,
        args.figure_height,
    )
    image_outputs = save_formats(fig, out_dir, stem, formats)
    plt.close(fig)
    gpx_hash_after = sha256_file(gpx_path)
    if gpx_hash_after != gpx_hash_before:
        raise SystemExit("Input integrity failure: GPX changed during read-only plotting")

    result = {
        "style_profile": STYLE_PROFILE,
        "outputs": image_outputs,
    }
    if args.write_plot_manifest:
        manifest = {
            "gpx": str(gpx_path),
            "gpx_sha256": gpx_hash_before,
            "gpx_hash_unchanged": True,
            "histogram": hist.name,
            "sample_id": sample_id,
            "style_profile": STYLE_PROFILE,
            "x_range": [args.x_min, args.x_max],
            "statistics": statistics,
            "style": {
                "experimental": {
                    "description": "uniform raw hollow red markers",
                    "marker_size_pt": EXPERIMENTAL_MARKER_SIZE,
                    "marker_step": args.marker_step,
                    "connecting_line": False,
                    "smoothed": False,
                },
                "calculation": {
                    "color": CALCULATION_COLOR,
                    "line_width_pt": CALCULATION_LINE_WIDTH,
                },
                "difference": {
                    "color": DIFFERENCE_COLOR,
                    "line_width_pt": DIFFERENCE_LINE_WIDTH,
                },
                "bragg_positions": {
                    "color": BRAGG_COLOR,
                    "line_width_pt": BRAGG_LINE_WIDTH,
                },
                "fit_statistics": {
                    "font_size_pt": FIT_STATISTICS_FONT_SIZE,
                    "layout": "fixed label, equals-sign, and value columns",
                },
                "hkl_labels": bool(args.show_hkl_labels),
                "background_subtracted": not args.include_background,
                "figure_inches": [args.figure_width, args.figure_height],
                "output_dpi": OUTPUT_DPI,
            },
            "outputs": image_outputs,
            "labels": [
                {
                    "two_theta": float(row["two_theta"]),
                    "label": row["label"],
                    "intensity": float(row["intensity"]),
                    "phase": row["phase"],
                }
                for row in labels
            ],
            "integrity": "GPX SHA-256 identical before and after plotting",
        }
        manifest_path = out_dir / f"{stem}_plot_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result["manifest"] = str(manifest_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
