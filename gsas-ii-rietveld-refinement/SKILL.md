---
name: gsas-ii-rietveld-refinement
description: Run careful GSAS-II Rietveld refinement and archive final powder XRD results without generating figures. Use when the user asks for GSAS-II/GSAS/EXPGUI powder refinement, Rietveld refinement, XRD refinement, CIF-based refinement, reducing Rwp without distortion, comparing refined cells, organizing final CIF/XRD/GPX/LST/reports, or cleaning intermediate refinement files. Do not use for plotting or restyling Rietveld figures; use the separate `rietveld-plotting` skill for that.
---

# GSAS-II Rietveld Refinement

Use this skill to refine powder XRD data with a local GSAS-II installation and archive only the final defensible results. Treat Rwp as one diagnostic, not the objective. Use the bundled scripts rather than writing a sample-specific refinement driver.

## Defaults

- Use real GSAS-II through a Python interpreter that can import `GSASIIscriptable`; do not present simulated patterns as refinement. Read `GSASII_PYTHON` and `GSASII_DIR` when configured.
- Use `GSASII_REFINEMENT_STAGING`, or `~/GSAS-II_refinement_staging/` by default, for generated intermediate files.
- Archive final results under `GSASII_REFINEMENT_ARCHIVE/<cif-key>/<sample-id>/`, or `~/GSAS-II_refinement_results/<cif-key>/<sample-id>/` by default.
- Keep the final XRD data, exact instrument file, result/source CIF, selected `.gpx`, `.lst`, report, and manifest together in the same sample folder.
- Keep `candidate_summary.json` and `report_validation.json` with every final archive.
- Do not generate, restyle, copy, or archive Rietveld figures as part of this skill.
- After verifying the final archive, delete the run's process/intermediate files from the skill staging folder. Never delete original source XRD/CIF files.
- Require the user to supply the source CIF and instrument parameter file. Never silently substitute a material-specific structure or a private instrument file.

## Required references

Read these as needed:

- `references/workflow.md` for the deterministic branched GSAS-II procedure and portable invocation.
- `references/dialectical-review.md` for the mandatory self-debate gate before selecting a final result.
- `references/archive-policy.md` before final cleanup or moving results.

Use:

- `scripts/run_staged_refinement.py` to create the GPX candidates and `candidate_summary.json`.
- `scripts/select_refinement_candidate.py` to enforce the safety gate, export the final CIF, and bind selected file hashes into the candidate summary.
- `scripts/build_validate_refinement_report.py` to generate or validate the report, metric labels, and crystallographic uncertainties.
- `scripts/archive_refinement_results.py` to transactionally create the final archive and safely remove staging files.

## Workflow

1. Inspect inputs: XRD format/range/step, CIF space group/cell/occupancy, instrument parameter file, and user sample context.
2. Create a new run staging directory. Never work directly in the final archive.
3. Run `run_staged_refinement.py --plan-only` and record whether the instrument U/V/W profile is calibrated or uncalibrated.
4. Run the deterministic driver. It must lock the instrument profile first, refine scale/background, then branch from the same baseline into cell-only, Zero-only, simultaneous cell+Zero, cell-then-Zero, and Zero-then-cell sensitivity cases.
5. Release W or U/V/W only after geometry sensitivity. Free U/V/W is exploratory and forbidden with an uncalibrated profile.
6. Do not freely refine atom coordinates, element occupancies, vacancy content, or dopant occupancy unless there is independent evidence and constraints.
7. Read `candidate_summary.json`. Audit convergence, correlations, Durbin-Watson, cell/Zero path dependence, and full-range positive residual peaks.
8. For each candidate, write a short dialectical review: why the fit improved, why it might be false, and whether it survives.
9. Select one conservative final with `select_refinement_candidate.py`. Prefer the physically stable result over the lowest Rwp; never select a candidate that fails the automatic safety gate.
10. Generate the canonical report and require `report_validation.json` to have `status=pass`.
11. Archive with `archive_refinement_results.py`. It validates every required input, copies to a sibling temporary directory, verifies source/destination hashes, then atomically installs or replaces the archive.
12. Verify the final manifest before staging cleanup. If the user separately requests a figure, hand the final `.gpx` path to `rietveld-plotting` after refinement and cleanup are complete.

## Stop rules

Stop or ask the user before claiming a final structural conclusion when:

- Major peaks are unexplained by the current phase model.
- A residual audit finds large positive observed-minus-calculated peaks that are not accounted for by the current phase model, even when Rwp is numerically low.
- The imported instrument file has unusual assumptions such as `I(L2)/I(L1)=0` for a Cu Kalpha pattern and no calibrated instrument file was supplied.
- A candidate lowers Rwp through negative/nonphysical profile terms, severe SVD warnings, correlation at or above 95%, or background swallowing real peaks.
- The user wants dopant occupancy, vacancy content, or quantitative phase fractions without constraints and independent composition evidence.
- GSAS-II GUI has the same `.gpx` open; tell the user to reopen the saved project rather than saving the stale window.

## Reporting requirements

Every final report must include:

- Input paths and whether refinement used real GSAS-II.
- Space group, CIF/source model, instrument parameters, 2theta range, and staged parameter order.
- Final Rwp, Rp, R-bkg, wR-bkg, RF, RF², and GOF when available. Never use ambiguous `Rb` or `wRb` labels.
- Cell, Zero, and other refined values formatted from the GPX covariance as `value(esd)`, not hand-counted parentheses.
- The instrument-profile calibration declaration and the cell/Zero sensitivity comparison.
- Residual-peak audit: list the most important unexplained positive residual peaks, or explicitly state that no major residual peaks remain visible.
- Cleanup status: confirm process/intermediate files were deleted from staging, or explain why cleanup was intentionally deferred.
- A dialectical review table for accepted and rejected candidates.
- Paths and hashes for `candidate_summary.json` and the passing `report_validation.json`.
- A clear evidence boundary: what the refinement supports, what it does not prove, and what follow-up evidence is needed.
- An explicit statement that this skill did not generate a figure.
