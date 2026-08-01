# Temperature and operando plotting policy

## Scope and source gate

- This route visualizes already completed GSAS-II sequential refinement. It does not launch refinement, select a structural model, or write a GPX.
- Require one `sequential_results_forward.json` or `sequential_results_reverse.json`, its `sequential_audit.json`, every recorded source pattern, and every final GPX or segment GPX.
- Verify all recorded SHA-256 values before plotting and again after plotting. Stop on any mismatch.
- Accept audit status `pass` or `review`. Preserve `review` in the plot manifest and handoff report; a clean figure does not promote it to `pass`.
- Reject audit status `fail` by default. An explicitly requested troubleshooting render must carry a permanent diagnostic label on every image.
- Require a varying numeric series coordinate. Temperature series use measured temperature; constant-temperature battery sequences use synchronized time/voltage/capacity when provenance exists, otherwise explicit frame order.
- Never infer elapsed time or voltage from frame number alone. Frame order is a valid display coordinate but not a physical synchronization.

### Experimental-operando exception

- A raw-pattern heatmap or all-frame full-pattern stack is permitted without accepted sequential refinement only when a strict conversion audit, synchronized frame manifest, and synchronization audit are all present.
- Verify every converted pattern and, by default, every original binary source against its recorded SHA-256 before and after plotting.
- Require complete, one-to-one nearest-time synchronization within the declared tolerance. The synchronization audit must bind the exact manifest path and SHA-256 and keep one unique metadata-row match per frame. Reject reused metadata rows, stale or foreign audits, interpolation, unmatched frames, non-monotonic time, smoothing, or background-subtracted conversion.
- Label the figure permanently as experimental operando XRD and state that it is not per-frame Rietveld refinement. The heatmap must retain every frame. A stack defaults to every frame but may use explicit uniform representative sampling for readability when the manifest records all selected frame indices, the full source series remains hash-verified, and the complete synchronized electrochemical trajectory remains displayed. Record intensity scaling and true time-coordinate provenance. Do not show R factors, Bragg positions, refined cells, or structural trajectories on this route.

## Figure set

Generate three complementary views rather than forcing all information into one crowded panel:

1. **Sequentially stacked observed patterns** — all frames in acquisition order and unsmoothed. Temperature uses the restrained blue-neutral-red scale; generic operando order uses a perceptually ordered sequential palette.
2. **Series-coordinate contour map** — the same observed arrays linearly resampled onto a common 2theta grid for display only. Use the physical coordinate only when it is strictly monotonic; otherwise use frame order so heating/cooling or cycling history is not silently reordered.
3. **Refined cell trajectories** — symmetry-independent cell parameters plus volume for one explicitly selected phase, with formal GSAS-II ESD error bars wherever present and the same audited series coordinate on the x axis.

When requested, add representative Rietveld fits separately with `make_rietveld_plot.py`. Select endpoints, a midpoint, and justified transition-boundary frames; do not cherry-pick only low-Rwp frames.

## Origin-derived visual contract

- White background, full black box, no grid, outward ticks on the left and bottom, and minor ticks.
- Arial when available and a compatible sans-serif fallback otherwise.
- Use proportional paper-panel typography derived from the Origin profile: `10.5 pt` axis labels, `8.2 pt` tick labels, `7 pt` annotations, and `12 pt` panel letters at the default 5–6 inch canvas scale. These preserve the Origin size hierarchy without mechanically putting 36 pt labels on a small Rietveld panel.
- Use black/dark-grey structural trajectories with restrained red hollow markers and thin formal uncertainty bars.
- Use a perceptually ordered `magma` intensity map rather than a rainbow map. Use a restrained blue-neutral-red map only for temperature-coded stacks; use `viridis` for generic frame/time progression.
- Use `viridis` for the experimental-operando heatmap and restrained red/blue/grey markers for synchronized charge/discharge/hold voltage points. Use 0.5 V major ticks by default and keep the electrochemical legend outside the voltage data axes.
- Profile curves may overlap adjacent vertical offsets for explicitly requested peak emphasis, but the manifest must record the overlap-enabled scaling and titles, panel letters, axis labels, legends, and annotations must remain clear of data lines.
- A single disclosed power-law display exponent below one may lift weak peaks when explicitly requested. Apply the same exponent to every displayed window, preserve peak positions and unsmoothed source arrays, add automatic upper-axis padding, and do not interpret transformed peak heights quantitatively.
- A promotional layout may shorten the title and separate panel letters, legend, and a wrapped disclosure footer from the data areas. It must retain the experimental-data and no-per-frame-Rietveld boundary rather than removing it for aesthetics.
- A promotional heatmap uses a dedicated colorbar row and a dedicated electrochemical-legend column; colorbar title, colorbar ticks, panel labels, voltage trajectory, and figure title must not share the same text or data region.
- An explicitly requested clean promotional export may omit the visible title, panel letters, and disclosure footer while retaining axes, colorbar, and electrochemical legend. Preserve all omitted disclosure text and transformation metadata in the manifest, and require an external caption when the image is shared.
- Export `600 dpi` PNG and editable SVG by default for the temperature route.

## Display transformations and disclosure

- Never smooth raw patterns, peak trajectories, or refined cell parameters.
- Default stacked/contour intensity mode is one global linear normalization across all frames. It changes units only and preserves between-frame scale.
- Allow raw intensity or explicitly requested per-frame normalization. Per-frame normalization destroys between-frame intensity comparability and must be recorded as a display-only transformation.
- The contour view uses linear interpolation only to place frames on one common 2theta grid; it is not a refinement input and must be disclosed in the plot manifest.
- The default contour upper colour limit is the `99.5th` percentile so weak reflections remain visible. Record the percentile and numerical limits; do not alter the underlying arrays.
- Use the intersection of all frame 2theta ranges by default. Never extrapolate a frame beyond its measured range.
- GSAS FXYE 2theta values are converted from centidegrees as required by that format. Generic XY/XYE coordinates are used as written.
- The experimental-operando default is a disclosed global `log1p` transform of non-negative counts with no smoothing or background subtraction. It preserves between-frame ordering but not linear count units. Per-frame normalization must be explicitly selected and is not quantitatively comparable between frames.

## Multiphase and uncertainty rules

- If more than one refined phase is common to the series, require an explicit phase name. Do not choose the major phase silently.
- Plot formal ESDs from the selected direction exactly as exported. Do not substitute forward/reverse path spread for a formal ESD.
- Forward and reverse refinement directions are algorithmic sensitivity tests, not experimental heating and cooling branches. Never label them as heating/cooling.
- A heating/cooling label may be used only when the experimental manifest explicitly contains branch provenance.
- At constant temperature, label plots by time, voltage, capacity, scan, or frame according to the actual audited metadata. Do not add an electrochemical curve when XRD/electrochemistry synchronization is missing.

## Literature-informed layout, not copied data

The layout is informed by recurring conventions in published variable-temperature and operando-XRD studies: full temperature stacks with selected peak windows, stacked and contour maps, representative refinements, and lattice-parameter trajectories. Examples include Li et al., *Angewandte Chemie International Edition* (DOI `10.1002/anie.202419300`) and the ZrNb14O37 study in *ACS Applied Materials & Interfaces* (DOI `10.1021/acsami.9b05841`). The skill does not redistribute paper files or data, and visual precedent is never treated as evidence for a user's sample.

## Acceptance checklist

- All input hashes match before and after plotting.
- Audit status, selected direction, phase, series key/range, frame count, and intensity mode are recorded.
- Every stacked frame is present and unsmoothed, or an explicit representative step and exact selected frame list are recorded without weakening full-source verification.
- Contour vertical-axis choice preserves acquisition history; interpolation and percentile clipping are disclosed.
- Cell panels show only parameters actually exported for the selected phase and retain formal ESDs.
- PNG and SVG are readable at publication size with no clipping, accidental grids, rainbow palette, or hidden diagnostic-fail status.
- Experimental-operando figures retain the no-per-frame-Rietveld label and record complete synchronization and hash provenance in the plot manifest.
