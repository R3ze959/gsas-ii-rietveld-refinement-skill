# Rietveld plotting policy

## Locked-profile rule

- Treat the visual contract below as the `locked-reference-v2` default profile. Version 2 preserves the single-phase appearance and adds phase-resolved multiphase Bragg colors and legend entries.
- Do not silently change marker density, marker size, line widths, colors, typography, alignment, background mode, canvas ratio, or output resolution.
- Apply a deviation only when explicitly requested for the current figure. Preserve the locked profile for future figures unless the user explicitly asks to update the Skill.

## Source integrity

- Accept a final GSAS-II `.gpx` as the authoritative plotting source.
- Load the GPX read-only. Never run refinement cycles or save the project.
- Read observed, calculated, background, and difference arrays from the selected powder histogram.
- Read Bragg positions, HKL values, intensities, and phase names from the GPX reflection lists.

## Visual contract

- Subtract the fitted GSAS-II background from both observed and calculated profiles for the default display. Keep the difference curve unchanged because `(Observed - Background) - (Calculated - Background) = Observed - Calculated`.
- Do not draw a red connecting line. Plot unsmoothed raw `Observed - Background` values as hollow red circles in the default mode, or raw `Observed` values with `--include-background`; retain the original GPX arrays, statistics, and difference curve unchanged.
- Sample the complete experimental profile at `--marker-step 1` by default and use uniform hollow circles (`2.05 pt`) so every measured point is displayed. Do not vary marker size or style with peak height.
- Use `--marker-step 2` or higher only when the user explicitly requests reduced marker density.
- Plot the calculated profile as a continuous black `0.48 pt` line through the hollow experimental markers.
- Plot the observed-minus-calculated curve as a blue `0.50 pt` line (`#1f4ed8`) with a fixed vertical offset.
- Put short `0.45 pt` Bragg ticks between the main profile and difference curve.
- Detect single- versus multiphase mode only from the selected histogram's GPX reflection lists. Keep the established green (`#1b9e77`) one-row geometry and generic `Bragg position` legend entry for one phase. For two or more phases, the default `auto` layout creates one distinctly colored Bragg row per phase, uses the exact GPX phase name as the matching upper-right legend label, removes the generic `Bragg position` entry, preserves coincident reflections in every phase row, and places the difference curve below all rows. Append `(xx.xx wt%)` only when `ComputeMassFracs()` supplies a complete, normalized set with finite positive covariance ESDs for all displayed phases. Preserve the undisplayed ESDs and exact source in the manifest. These GPX-derived percentages are crystalline fractions over all modeled displayed phases; they are not automatically sample-role-normalized or corrected for amorphous content. Do not infer percentages from peak heights or normalized raw Scale values. Do not infer a material or space-group label that is absent from the GPX, and do not duplicate the phase names beside the lower rows.
- A combined multiphase Bragg row is an explicit diagnostic override only. Record the override in the plot manifest and do not imply phase-resolved reflection assignment from that row.
- Show accurately named `Rwp`, `Rp`, and `GOF` from the GPX in a compact upper-right block at `5.8 pt` by default. Use the same sans-serif font family, normal weight, and font size for all three lines, including subscripts and numerals; use fixed label, equals-sign, and value columns so both the first letters and equals signs align vertically.
- Hide HKL text labels by default for a clean reference-style figure. When requested, keep labels centered at their true GSAS-II 2theta positions and use vertical clearance or subtle leader lines instead of horizontal shifts.
- Select up to eight intense, separated reflections only when HKL text labels are enabled.
- Use Arial when available and a compatible sans-serif fallback otherwise.
- Use the compact default canvas of `4.05 × 3.35 inches` (width:height about `1.21:1`) so the x-axis remains only slightly longer than the y-axis. Preserve the established font sizes, axes, tick widths, and 600 dpi export unless the user explicitly requests another setting.
- Keep axes, tick marks, and legend legible at publication size.
- Inspect the rendered PNG before accepting it.

## Mandatory integrity and visual verification

- Compute and compare the GPX SHA-256 hash before and after plotting; require an exact match.
- Verify every visible measured point is represented at `marker-step 1`.
- Verify the experimental points are unsmoothed `2.05 pt` hollow red circles with no red connecting line.
- Verify the calculated and difference elements use the locked colors and line widths. Verify single-phase Bragg ticks remain green; for multiphase projects, verify each phase uses its recorded distinct color.
- For multiphase projects, verify that phase-row and phase-legend counts, names, colors, optional covariance-backed wt% labels, and per-row reflection counts match the selected histogram's GPX data and that the expanded legend does not overlap fit statistics or clip at the axes.
- Verify the statistics block uses fixed label, equals-sign, and value columns.
- Verify the final canvas ratio, visible range, clipping, and `600 dpi` PNG export before acceptance.

## Output policy

- Use PNG as the default and preferred retained output.
- Use `TIF`, `PDF`, or `SVG` only when the user explicitly requests a submission format.
- For a single-pattern GPX figure, do not keep plot CSV files or debug manifests unless the user requests them. Temperature-series and experimental-operando routes must retain their machine-readable plot manifest because it records source hashes, audit state, selected frames, synchronization, and display-only transformations.
- Keep only the accepted final image after visual verification; remove plotting drafts.
- Default to `RIETVELD_PLOT_OUTPUT/<sample-id>/`, or `~/Rietveld_plot_results/<sample-id>/` when the environment variable is unset, when no destination is supplied.

## Boundary with refinement

- Report visible residual or peak-shape problems, but do not change the GPX.
- Route any request to alter Rwp, structural parameters, background, instrument terms, profile terms, phase models, or occupancies to `gsas-ii-rietveld-refinement`.
- Treat a final plot as a visualization of an accepted refinement, not as validation of the structural interpretation.
