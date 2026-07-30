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

- Subtract the fitted GSAS-II background from both observed and calculated profiles by default. Keep the difference curve unchanged because `(Observed - Background) - (Calculated - Background) = Observed - Calculated`.
- Plot unsmoothed raw `Observed - Background` values as hollow red circles, or raw `Observed` values with `--include-background`. Never draw a red connecting line.
- Use `--marker-step 1` and uniform `2.05 pt` hollow circles so every measured point is displayed.
- Use `--marker-step 2` or higher only when the user explicitly requests reduced marker density.
- Plot the calculated profile as a continuous black `0.48 pt` line.
- Plot the observed-minus-calculated curve as a blue `0.50 pt` line (`#1f4ed8`) with a fixed vertical offset.
- Put short green `0.45 pt` Bragg ticks (`#1b9e77`) between the main profile and difference curve.
- Show accurately named `Rwp`, `Rp`, and `GOF` from the GPX in a compact upper-right block at `5.8 pt`. Use one sans-serif font, normal weight, and fixed label, equals-sign, and value columns.
- Hide HKL labels by default. When requested, keep labels centered at their true GSAS-II 2theta positions and use vertical clearance or subtle leader lines instead of horizontal shifts.
- Select up to eight intense, separated reflections only when HKL labels are enabled.
- Use Arial when available and a compatible sans-serif fallback otherwise.
- Use a `4.05 × 3.35 inch` canvas and `600 dpi` raster export unless the user explicitly requests another setting.

## Mandatory integrity and visual verification

- Compute and compare the GPX SHA-256 hash before and after plotting; require an exact match.
- Verify every visible measured point is represented at `marker-step 1`.
- Verify the experimental points are unsmoothed `2.05 pt` hollow red circles with no red connecting line.
- Verify the calculated, difference, and Bragg elements use the locked colors and line widths.
- Verify the statistics block uses fixed label, equals-sign, and value columns.
- Verify the final canvas ratio, visible range, clipping, and `600 dpi` PNG export before acceptance.

## Output policy

- Use PNG as the default retained output.
- Use TIF, PDF, or SVG only when explicitly requested.
- Do not keep plot CSV files or plot manifests unless requested for reproducibility or debugging.
- Keep only the accepted final image after visual verification.
- Require the caller to provide an explicit output directory.

## Boundary with refinement

- Report visible residual or peak-shape problems, but do not change the GPX.
- Route requests to alter Rwp, structural parameters, background, instrument terms, profile terms, phase models, or occupancies to `gsas-ii-rietveld-refinement`.
- Treat a final plot as a visualization of an accepted refinement, not as validation of the structural interpretation.
