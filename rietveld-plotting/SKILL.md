---
name: rietveld-plotting
description: Create, restyle, and verify publication-ready Rietveld refinement figures from final GSAS-II `.gpx` projects without changing refinement parameters. Use when the user asks to plot Rietveld results, draw observed/calculated/difference curves, add Bragg ticks or HKL labels, re-export a GSAS-II fit figure, change the 2theta range or panel label, or prepare a PNG figure from an already selected refinement. Do not use this skill to refine XRD data, select structural models, lower Rwp, or modify a `.gpx`; use `gsas-ii-rietveld-refinement` for those tasks.
---

# Rietveld Plotting

Create figures only from a final, defensible GSAS-II `.gpx`. Keep plotting downstream from refinement and never alter structural, profile, background, or instrument parameters.

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
- Draw the calculated profile as a continuous black `0.48 pt` line, the unchanged offset difference profile as a blue `0.50 pt` line (`#1f4ed8`), and GSAS-II reflection positions as green `0.45 pt` ticks (`#1b9e77`).
- Show Rwp, Rp, and GOF from the GPX in the upper-right by default at `5.8 pt`. Render all three lines with one sans-serif math font and normal weight; use fixed label, equals-sign, and value columns so both the first letters and equals signs align vertically.
- Hide HKL text labels by default; show them only when requested.
- Hide y-axis numeric labels for relative-intensity figures unless absolute values matter.
- Use Arial when available, preserve the established axis and tick typography, and export raster output at `600 dpi`.
- Produce separate sample figures unless the user explicitly requests a combined panel.

## Required reference

Read `references/plotting-policy.md` before generating or revising a figure.

## Workflow

1. Confirm that the input is a final `.gpx`, not an unrefined or rejected candidate.
2. Record the GPX SHA-256 hash before loading it.
3. Load the project read-only and identify the requested powder histogram, phases, available 2theta range, calculated profile, fitted background, unchanged difference curve, fit statistics, and reflection lists.
4. Resolve only explicit user overrides. Otherwise invoke `scripts/make_rietveld_plot.py` with the locked defaults above.
5. Record the GPX SHA-256 hash again after rendering. Stop and report an integrity failure if it differs.
6. Inspect the generated image against the acceptance checklist below. Do not accept the image from command success alone.
7. Regenerate only when a visible plotting defect is found. Do not respond to a fit defect by changing refinement parameters.
8. Keep the accepted image and delete plotting-only drafts or debug manifests unless the user asks to preserve them.
9. Report the source GPX, unchanged hash status, histogram, phase names, plotted range, output format, background-display mode, marker step, and whether Bragg/HKL positions came from the GSAS-II reflection list.

## Acceptance checklist

- Confirm a compact width:height ratio of about `1.21:1`, the requested 2theta range, and no clipped axes, legend, or statistics.
- Confirm one unsmoothed `2.05 pt` hollow red circle for every visible measured point at the locked `marker-step 1`; confirm there is no red connecting line and no peak-height-dependent styling.
- Confirm the continuous calculated line is black and remains visible through the markers.
- Confirm the difference line is blue (`#1f4ed8`), vertically offset, and still represents the unchanged `Observed - Calculated` array.
- Confirm green Bragg ticks (`#1b9e77`) come directly from the selected GPX histogram reflection lists.
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

Optional controls include `--histogram-index`, `--panel`, `--show-y-values`, `--show-hkl-labels`, `--hide-fit-statistics`, `--include-background`, `--figure-width`, `--figure-height`, `--max-labels`, `--label-separation`, `--marker-step`, `--stem`, and `--formats`.

## Stop rules

- Stop if the GPX lacks calculated profile data, a difference curve, or reflection lists required by the requested figure.
- Stop and route to `gsas-ii-rietveld-refinement` if the user asks to change the structure model, background, cell, zero, peak shape, R factors, occupancies, or any other refined parameter.
- Do not invent, borrow, or manually shift Bragg/HKL assignments from a reference image.
- Do not call a visually attractive figure evidence of a valid refinement; plotting does not replace residual or model audits.

## Reporting requirements

- State that plotting was read-only and did not refine or modify the GPX.
- Link the source GPX and final image.
- State the histogram, phase names, 2theta range, and output format.
- State whether Bragg positions and HKL labels were read directly from the GSAS-II reflection list.
- State whether the displayed observed/calculated profiles include or subtract the fitted background, and disclose any display-only smoothing.
- Note any visible unresolved fit defects without attempting to reinterpret or repair them in this skill.
