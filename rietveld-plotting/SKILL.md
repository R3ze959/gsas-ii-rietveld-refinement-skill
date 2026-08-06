---
name: rietveld-plotting
description: Create, restyle, and verify publication-ready single- or multiphase Rietveld, temperature-series, or operando XRD figures without changing refinement parameters. Use for observed/calculated/difference curves, phase-separated Bragg rows, accepted GSAS-II `.gpx` plots, audited temperature/time/frame series, covariance-backed phase-fraction trajectories, experimental operando XRD plus synchronized electrochemistry, contour maps, and refined lattice-parameter trajectories from accepted sequential-result exports. Do not use this skill to refine XRD data, select structural models, lower Rwp, or modify a `.gpx`; use the appropriate refinement skill for those tasks.
---

# Rietveld Plotting

Create Rietveld figures only from a final, defensible GSAS-II `.gpx`, or from a hash-verified sequential result plus its audit and recorded source patterns. A separate experimental-operando route may display hash-verified converted patterns with synchronized electrochemistry, but it must state on the image and in its manifest that no per-frame Rietveld refinement is claimed. Keep plotting downstream from refinement and never alter structural, profile, background, or instrument parameters.

## Route before plotting

- One accepted powder histogram in a final GPX: use the locked single-pattern route below.
- A high- or low-temperature series with `sequential_results_<direction>.json`, `sequential_audit.json`, varying temperature metadata, final GPX hashes, and source-pattern hashes: use the temperature-series route.
- Constant-temperature operando frames varying with a provenance-backed time, voltage, capacity, scan, or frame coordinate: use the operando route. Do not relabel frame order as time or voltage.
- Converted raw operando patterns with a conversion audit, synchronized manifest, and synchronization audit: use the experimental-operando route. It may show experimental intensity and electrochemistry only; never add refined cells, Bragg ticks, R factors, or a Rietveld claim.
- Raw temperature patterns without accepted sequential results or a supported experimental-operando audit bundle: stop. They may be drawn as raw XRD data by a general scientific plotting workflow, but not presented as refined trajectories.

## Locked default contract

Treat every item in this section as a locked default. Do not silently substitute, infer, optimize, or restyle any item. Apply an override only when the user explicitly requests it for the current figure. Do not convert a one-off override into a new Skill default unless the user explicitly asks to update the Skill.

- Read the final `.gpx` with real GSAS-II through `GSASII_PYTHON` (or another explicitly supplied Python interpreter that imports `GSASIIscriptable`) and `GSASII_DIR`.
- Use `scripts/make_rietveld_plot.py`; do not retype an ad hoc plotting script.
- Write new figures under `RIETVELD_PLOT_OUTPUT/<sample-id>/`, or `~/Rietveld_plot_results/<sample-id>/` when the environment variable is unset, unless the user specifies another destination.
- Export PNG only by default.
- Use a `10-60°` 2theta window unless the data range or user request requires another range.
- Use a compact `4.05 × 3.35 inch` canvas by default (width:height about `1.21:1`), keeping the x-axis only slightly longer than the y-axis without changing the established font sizes.
- Subtract the fitted GSAS-II background from observed and calculated intensities for the default display. Keep `Difference = Observed - Calculated` unchanged. Use `--include-background` only when the user requests the conventional raw-intensity view.
- Draw the entire experimental profile with uniform `2.05 pt` hollow red circles at a default `--marker-step 1`, so every measured point is shown. Do not draw a red connecting line or vary marker size by peak height.
- Use `--marker-step 2` or higher only when the user explicitly requests reduced marker density. Never smooth plotted experimental marker intensities, replace the raw GPX arrays, or use altered values for refinement statistics.
- Draw the calculated profile as a continuous black `0.48 pt` line and the unchanged offset difference profile as a blue `0.50 pt` line (`#1f4ed8`). Draw Bragg ticks at `0.45 pt` directly from the GPX reflection lists.
- Detect phase count from the selected histogram's reflection lists. For one phase, preserve the established single green Bragg row (`#1b9e77`) and the `Bragg position` legend entry exactly. For two or more phases, `--bragg-layout auto` must create one row per phase, assign a deterministic distinct color to each phase, move the difference curve below all rows, and replace the generic `Bragg position` legend entry with one colored entry per exact GPX phase name. When GSAS-II `ComputeMassFracs()` returns a complete, normalized, covariance-backed fraction set for all plotted phase rows, append the value as `Phase name (xx.xx wt%)`; otherwise show names only. Never derive a percentage from peak height or raw HAP Scale, never add `100%` to a single-phase legend, and record the values, ESDs and source in the plot manifest. Treat these as modeled crystalline mass fractions, not amorphous-corrected or sample-role-normalized fractions. Do not repeat phase names beside the lower rows. Keep coincident reflections in their respective phase rows; never collapse them across phases. Use `--bragg-layout combined` only when the user explicitly requests a diagnostic combined row.
- Show Rwp, Rp, and GOF from the GPX in the upper-right by default at `5.8 pt`. Render all three lines with one sans-serif math font and normal weight; use fixed label, equals-sign, and value columns so both the first letters and equals signs align vertically.
- Hide HKL text labels by default; show them only when requested.
- Hide y-axis numeric labels for relative-intensity figures unless absolute values matter.
- Use Arial when available, preserve the established axis and tick typography, and export raster output at `600 dpi`.
- Produce separate sample figures unless the user explicitly requests a combined panel.

## Required reference

Read `references/plotting-policy.md` before generating or revising a single-pattern figure. Read `references/temperature-series-policy.md` for temperature or operando sequential figures.

## High-/low-temperature sequential route

Use `scripts/make_temperature_series_plot.py`; do not reconstruct the sequence with an ad hoc notebook. The script reads observed profiles from the hash-verified recorded input patterns and refined cells/formal ESDs from one selected direction's GSAS-II JSON export. It verifies every recorded pattern and final GPX hash before and after plotting and never imports or saves GSAS-II.

Default outputs are a temperature-coloured stacked pattern, a sequential-intensity contour map, one refined cell-parameter figure per requested phase, an automatic phase-fraction figure when at least two covariance-backed mass-fraction series are available, and one machine-readable plot manifest. Export PNG at 600 dpi plus editable SVG. Use the common 2theta intersection rather than forcing the single-pattern `10-60°` window. Use global intensity normalization by default because it preserves between-frame scale; allow per-frame normalization only as an explicitly disclosed display transformation. Never smooth profiles or refined trajectories.

For multiphase results, require `--phase` instead of guessing which phase the user means; accept one phase, a comma-separated phase list, or `--phase all`. Plot each selected phase's cell parameters separately. A phase that is absent from a frame must appear as a gap, never a zero or interpolated value. Phase fractions must come from the sequential export's covariance-backed `mass_fractions`, be shown in wt%, retain formal ESDs, and break across phase absence. Mark declared phase-set changes, and stop if an active phase lacks a fraction. `pass` and `review` audits may be drawn, but report `review` as scientifically unresolved. Stop on `fail` unless the user explicitly requests a diagnostic; a failed diagnostic must retain the permanent `DIAGNOSTIC ONLY — SEQUENTIAL AUDIT FAIL` label.

When selected-temperature Rietveld fits are also requested, choose defensible frames (normally endpoints, midpoint, and transition boundaries) from the accepted sequence and render each with the existing `make_rietveld_plot.py`. Do not select only the lowest-Rwp frames or borrow Bragg assignments from a paper.

## Constant-temperature battery operando route

Use the same deterministic script with `--series-key`. Prefer a synchronized physical coordinate such as `time_min`, `voltage_V`, or capacity only when that field is present in every frame's audited metadata. When timestamps or synchronization provenance are absent, use `source_frame` or `order`, label the axis `Frame`, and state that voltage/time alignment is unavailable. The contour map and numerical trajectories retain every frame. A publication stack may use declared uniform representative sampling when all source frames remain hash-verified and the plot manifest records the selected frame indices; never imply that a representative stack contains every frame.

## Experimental-operando route without accepted sequential refinement

Use `scripts/make_operando_xrd_plot.py` only when all three inputs are present: a strict STOE conversion audit, a synchronized manifest CSV, and the hash-bound one-to-one nearest-time synchronization audit created with the current `build_sequential_manifest.py`. The script verifies every recorded converted-pattern hash and, by default, every original RAW source hash before and after plotting. It rejects incomplete synchronization, reused metadata rows, a stale or foreign manifest audit, interpolation, altered files, smoothing, background-subtracted conversion, path disagreements, and non-monotonic acquisition time.

The output contains either the experimental XRD heatmap or a full-pattern stack, together with the complete synchronized voltage trajectory. It carries a permanent note that it is not per-frame Rietveld refinement and records `per_frame_rietveld_claimed=false` in the plot manifest. The heatmap defaults to a disclosed `log1p` display transform with no smoothing or background subtraction. Use `--view stacked --intensity-mode per-frame` when the user prioritizes seeing weak reflections; the manifest must state that this display normalization does not preserve between-frame intensity scale. For a paper-like representative stack, use an explicit `--frame-step`, declare retained frame IDs, and use `--x-windows '6,8.5;14,21'` only for honest broken-axis peak windows. `--allow-profile-overlap` may enlarge peaks across adjacent offset baselines only when the user explicitly requests peak emphasis; disclose it as display scaling and keep all text and legends outside the plotted profiles. When the user explicitly asks to lift weak peaks, `--peak-gamma` may be set below 1; record the exponent, state that it is nonlinear display-only intensity scaling, retain one common transform across all windows, and never describe the transformed height as quantitative intensity. Keep the full electrochemical trace, use a `0.5 V` major interval unless the user specifies otherwise, and place its legend outside the data axes.

For an explicitly requested promotional operando figure, `--promotional-layout` may use a shorter title, put panel letters above the data boxes, retain the legend in a dedicated blank column, and wrap the scientific disclosure into two centered lines below the axes. In a heatmap, it must also allocate a dedicated colorbar row so the colorbar title and tick labels cannot collide with the figure title or heatmap. This option changes spacing only: it must not remove the no-per-frame-Rietveld statement, source verification, selected-frame disclosure, or nonlinear-display exponent.

When the user explicitly requests a figure with no visible annotations beyond axes, colorbar, and legend, add `--clean-figure` together with `--promotional-layout`. Omit the visible title, panel letters, and footer, but retain every disclosure and source-integrity result in the plot manifest. State on handoff that an external caption must identify the experimental display transform and that the figure is not per-frame Rietveld refinement.

## Workflow

1. Confirm that the input is a final `.gpx`, not an unrefined or rejected candidate.
2. Record the GPX SHA-256 hash before loading it.
3. Load the project read-only and identify the requested powder histogram, ordered phase list, available 2theta range, calculated profile, fitted background, unchanged difference curve, fit statistics, and phase-specific reflection lists.
4. Resolve only explicit user overrides. Otherwise invoke `scripts/make_rietveld_plot.py` with the locked defaults above.
5. Record the GPX SHA-256 hash again after rendering. Stop and report an integrity failure if it differs.
6. Inspect the generated image against the acceptance checklist below. Do not accept the image from command success alone.
7. Regenerate only when a visible plotting defect is found. Do not respond to a fit defect by changing refinement parameters.
8. Keep the accepted image and delete plotting-only drafts or debug manifests unless the user asks to preserve them.
9. Report the source GPX, unchanged hash status, histogram, phase names, Bragg-row layout, plotted range, output format, background-display mode, marker step, and whether Bragg/HKL positions came from the GSAS-II reflection list.

## Acceptance checklist

- Confirm a compact width:height ratio of about `1.21:1`, the requested 2theta range, and no clipped axes, legend, or statistics.
- Confirm one unsmoothed `2.05 pt` hollow red circle for every visible measured point at the locked `marker-step 1`; confirm there is no red connecting line and no peak-height-dependent styling.
- Confirm the continuous calculated line is black and remains visible through the markers.
- Confirm the difference line is blue (`#1f4ed8`), vertically offset, and still represents the unchanged `Observed - Calculated` array.
- Confirm Bragg ticks come directly from the selected GPX histogram reflection lists.
- For multiphase GPX files, confirm one distinctly colored row and one matching upper-right legend entry per exact GPX phase name, no generic `Bragg position` legend entry, no cross-phase reflection collapse, and enough vertical clearance between the lowest Bragg row and the difference curve. If percentages are displayed, confirm every plotted phase has a finite positive covariance ESD and that the displayed crystalline mass fractions sum to `100%` within rounding. For one phase, confirm the original green row, `Bragg position` legend entry, geometry, and absence of a redundant `100%` suffix remain unchanged.
- Confirm `Rwp`, `Rp`, and `GOF` are read from the GPX, share one `5.8 pt` sans-serif style, and use three fixed columns with both the first letters and equals signs vertically aligned.
- Confirm HKL text and y-axis numeric values remain hidden unless explicitly requested.
- Confirm PNG is exported at `600 dpi` and the GPX hash is identical before and after plotting.

## Command

```bash
"$GSASII_PYTHON" "${CODEX_HOME:-$HOME/.codex}/skills/rietveld-plotting/scripts/make_rietveld_plot.py" \
  --gpx /abs/path/<sample-id>_refinement.gpx \
  --sample-id SampleID \
  --out-dir "${RIETVELD_PLOT_OUTPUT:-$HOME/Rietveld_plot_results}/SampleID" \
  --x-min 10 --x-max 60
```

Optional controls include `--histogram-index`, `--panel`, `--show-y-values`, `--show-hkl-labels`, `--hide-fit-statistics`, `--include-background`, `--figure-width`, `--figure-height`, `--max-labels`, `--label-separation`, `--marker-step`, `--bragg-layout`, `--stem`, and `--formats`.

Temperature-series command:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/rietveld-plotting/scripts/make_temperature_series_plot.py" \
  --results /abs/path/results/sequential_results_forward.json \
  --audit /abs/path/results/sequential_audit.json \
  --phase all \
  --phase-fractions auto \
  --out-dir "${RIETVELD_PLOT_OUTPUT:-$HOME/Rietveld_plot_results}/SeriesName" \
  --sample-id SeriesName
```

Temperature controls include `--temperature-key`, `--phase` (one name, comma-separated names, or `all`), `--phase-fractions auto|require|hide`, `--cell-parameters`, `--x-min`, `--x-max`, `--intensity-mode`, `--stack-offset`, `--contour-vmax-percentile`, and `--formats`. Use `--phase-fractions require` when a multiphase quantitative panel is mandatory; use `hide` only when explicitly requested. Use `--allow-failed-audit-for-diagnostic` only for explicitly requested troubleshooting.

For a constant-temperature operando series, replace `--temperature-key` with `--series-key source_frame` (or a provenance-backed physical coordinate) and optionally set `--series-label` and `--series-unit`. Use `--stack-max-labels` to limit annotations without dropping curves.

Experimental-operando command:

```bash
python "${CODEX_HOME:-$HOME/.codex}/skills/rietveld-plotting/scripts/make_operando_xrd_plot.py" \
  --manifest /abs/path/manifest.csv \
  --conversion-audit /abs/path/stoe_conversion_audit.json \
  --sync-audit /abs/path/manifest.csv.sync.json \
  --sample-id PublicOperandoSeries \
  --out-dir "${RIETVELD_PLOT_OUTPUT:-$HOME/Rietveld_plot_results}/PublicOperandoSeries"
```

Add `--view stacked --intensity-mode per-frame` for a full-pattern stack. The default `--frame-step 1` draws every frame; any larger step is a disclosed representative display and does not change source verification. `--x-windows` creates a broken-axis display only and never alters source patterns.

## Stop rules

- Stop if the GPX lacks calculated profile data, a difference curve, or reflection lists required by the requested figure.
- Stop if sequential results lack a varying numeric series coordinate, a scientific audit, formal source hashes, or refined cell data for the requested phase.
- Stop if a requested phase is never present, an active phase lacks requested cell values, an active phase lacks an exported mass fraction when fractions are required, or any fraction/ESD is nonphysical.
- Stop if an experimental-operando request lacks a complete conversion/synchronization audit bundle, or if it asks to present raw heatmaps as refined lattice trajectories.
- Stop and route to `gsas-ii-rietveld-refinement` if the user asks to change the structure model, background, cell, zero, peak shape, R factors, occupancies, or any other refined parameter.
- Do not invent, borrow, or manually shift Bragg/HKL assignments from a reference image.
- Do not call a visually attractive figure evidence of a valid refinement; plotting does not replace residual or model audits.

## Reporting requirements

- State that plotting was read-only and did not refine or modify the GPX.
- For a sequential series, report the selected direction, audit status, selected phases, phase-absence gaps, whether covariance-backed phase fractions were shown, fraction source/ESDs, coordinate provenance/range, frame count, intensity-display mode, contour clipping percentile, and unchanged source-hash status.
- For an experimental-operando series, report that no per-frame Rietveld refinement is claimed, plus frame count, synchronized time/voltage ranges, maximum synchronization error, intensity transform, and unchanged source-hash status.
- Link the source GPX and final image.
- State the histogram, phase names, 2theta range, and output format.
- State whether Bragg positions and HKL labels were read directly from the GSAS-II reflection list, whether multiphase rows were separate or combined, the phase-to-color mapping, and whether any displayed wt% labels came from covariance-backed GSAS-II crystalline mass fractions.
- State whether the displayed observed/calculated profiles include or subtract the fitted background, and disclose any display-only smoothing.
- Note any visible unresolved fit defects without attempting to reinterpret or repair them in this skill.
