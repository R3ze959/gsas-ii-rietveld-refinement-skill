---
name: gsas-ii-rietveld-refinement
description: Run careful GSAS-II Rietveld refinement and archive final powder XRD results. Use when the user asks for GSAS-II/GSAS/EXPGUI powder refinement, Rietveld refinement, XRD refinement, CIF-based refinement, complex crystalline material refinement, reducing Rwp without distortion, comparing refined cells, organizing final CIF/XRD/GPX/plots/reports, or cleaning intermediate refinement files.
---

# GSAS-II Rietveld Refinement

Use this skill to refine powder XRD data with a local GSAS-II installation and archive only the final defensible results. Treat Rwp as one diagnostic, not the objective.

## Defaults

- Use real GSAS-II via `GSASIIscriptable`; do not present simulated patterns as refinement.
- Set `GSASII_PYTHON` to the Python executable bundled with GSAS-II when running command-line workflows.
- Set `GSASII_DIR` to the local `GSAS-II` source directory when using `scripts/make_rietveld_plot.py`.
- Use `GSASII_REFINEMENT_STAGING` or `~/GSAS-II_refinement_staging/` for generated intermediate files.
- Archive final results under `GSASII_REFINEMENT_ARCHIVE` or `~/GSAS-II_refinement_results/<cif-key>/<sample-id>/`.
- Keep the final XRD data, result/source CIF, selected `.gpx`, `.lst`, fit plot, report, and manifest together in the same sample folder.
- Generate publication-style Python Rietveld plots from the final `.gpx` with `scripts/make_rietveld_plot.py`; keep only the final `*_python_rietveld.png` in the sample archive folder.
- Plot defaults: separate sample plots, `10-60°` range, real GSAS-II HKL labels, and PNG output only.
- After verifying the final archive, delete the run's process/intermediate files from the skill staging folder. Never delete original source XRD/CIF files.
- Use the user's supplied CIF or a clearly cited trusted reference CIF; never assume a compound-specific default structure model.

## Required references

Read these as needed:

- `references/workflow.md` for the staged GSAS-II procedure and generic structure-model defaults.
- `references/dialectical-review.md` for the mandatory self-debate gate before selecting a final result.
- `references/archive-policy.md` before final cleanup or moving results.

Use `scripts/archive_refinement_results.py` to create the final archive and safely remove staging files.
Use `scripts/make_rietveld_plot.py` to create reproducible final plots from `.gpx` files instead of hand-drawing figures in Origin or retyping ad hoc matplotlib code.

## Workflow

1. Inspect inputs: XRD format/range/step, CIF space group/cell/occupancy, instrument parameter file, and user sample context.
2. Create a new run staging directory. Never work directly in the final archive.
3. Build a new `.gpx` from CIF + XRD + instrument parameters. Save an unrefined project snapshot.
4. Refine in stages: scale/background -> cell -> zero -> U/V/W -> limited candidates such as background order, X/Y, microstrain, size, preferred orientation, or justified phases.
5. Do not freely refine atom coordinates, anion occupancy, mixed-site occupancy, dopant occupancy, or defect occupancy unless there is independent evidence and constraints.
6. Before selecting a final, audit the observed-minus-calculated curve and the full-range fit plot for major positive residual peaks. Low Rwp does not override visibly unexplained peaks.
7. For each candidate, write a short dialectical review: why the fit improved, why it might be false, and whether it survives.
8. Select one conservative final and, if useful, one clearly labeled lower-Rwp candidate. Prefer the physically stable result over the lowest Rwp.
9. Generate the final Python PNG plot into the same final sample archive folder that contains the selected `.gpx`, `.lst`, XRD, report, and CIF/export or source CIF copy.
10. Archive the selected final files by CIF key with `scripts/archive_refinement_results.py`.
11. Verify the archive manifest, then delete the current run's staging directory/process files only.

## Stop rules

Stop or ask the user before claiming a final structural conclusion when:

- Major peaks are unexplained by the current phase model.
- A residual audit finds large positive observed-minus-calculated peaks that are not accounted for by the current phase model, even when Rwp is numerically low.
- The imported instrument file has unusual assumptions such as `I(L2)/I(L1)=0` for a Cu Kalpha pattern and no calibrated instrument file was supplied.
- A candidate lowers Rwp through negative/nonphysical profile terms, severe SVD warnings, 100% parameter correlation, or background swallowing real peaks.
- The user wants dopant occupancy, mixed-site occupancy, anion deficiency, defect concentration, or quantitative phase fractions without constraints and independent composition evidence.
- GSAS-II GUI has the same `.gpx` open; tell the user to reopen the saved project rather than saving the stale window.

## Reporting requirements

Every final report must include:

- Input paths and whether refinement used real GSAS-II.
- Space group, CIF/source model, instrument parameters, 2theta range, and staged parameter order.
- Final Rwp/Rp/Rb if available, cell parameters, zero, profile terms, and any warnings from `.lst`.
- Residual-peak audit: list the most important unexplained positive residual peaks, or explicitly state that no major residual peaks remain visible.
- Final plot range and whether HKL labels were read from the GSAS-II reflection list; plotting output should be PNG only unless the user explicitly asks for more formats.
- Cleanup status: confirm process/intermediate files were deleted from staging, or explain why cleanup was intentionally deferred.
- A dialectical review table for accepted and rejected candidates.
- A clear evidence boundary: what the refinement supports, what it does not prove, and what follow-up evidence is needed.
