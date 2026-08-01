# Rietveld plotting policy

## Locked-profile rule

- Treat the visual contract below as the `locked-reference-v1` default profile.
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
- Put short green `0.45 pt` Bragg ticks (`#1b9e77`) between the main profile and difference curve.
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
- Verify the calculated, difference, and Bragg elements use the locked colors and line widths.
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
