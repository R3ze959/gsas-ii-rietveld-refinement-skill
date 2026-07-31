---
name: gsas-ii-rietveld-refinement
description: Actively classify a powder-XRD request, then run careful single-pattern, independent-batch, or manifest-driven sequential GSAS-II Rietveld refinement and archive final results without generating figures. Use for GSAS-II/GSAS/EXPGUI powder refinement, Rietveld refinement, XRD refinement, CIF-based refinement, ambiguous multiple-pattern inputs, detector images that may require 1D integration, operando/in-situ/in situ XRD, temperature/time/voltage series, sequential refinement, multiphase refinement, reducing Rwp without distortion, comparing refined cells, organizing final CIF/XRD/GPX/LST/reports, or cleaning intermediate files. Do not use for plotting or restyling Rietveld figures; use a separate plotting skill.
---

# GSAS-II Rietveld Refinement

Use this skill to classify and then refine either one powder XRD pattern or an ordered series of already integrated one-dimensional patterns with a local GSAS-II installation. Archive only defensible results. Treat Rwp as one diagnostic, not the objective. Use the bundled classifier and deterministic drivers rather than writing a sample-specific script.

## Mandatory first gate: classify the request

Before importing GSAS-II, creating a GPX, selecting refinement parameters, or creating a staging directory:

1. State the proposed category: single pattern, sequential series, independent batch, detector integration required, plotting handoff, or ambiguous.
2. Run `scripts/classify_refinement_request.py` with the supplied pattern, manifest, detector image, or accepted GPX inputs.
3. Continue only when its JSON has `status=ready` and names the intended numerical driver.
4. Stop numerical refinement for `status=blocked`, `status=needs_clarification`, or `status=handoff`.

Do not infer sequential refinement from multiple filenames alone. Multiple patterns without a valid manifest are ambiguous until the user declares an ordered sequence or independent samples. Read `references/refinement-routing.md` for the exact decision table and edge cases.

## Defaults

- Use real GSAS-II through a Python interpreter that can import `GSASIIscriptable`; do not present simulated patterns as refinement. Read `GSASII_PYTHON` and `GSASII_DIR` when configured.
- Use `GSASII_REFINEMENT_STAGING`, or `~/GSAS-II_refinement_staging/` by default, for generated intermediate files.
- Archive final results under `GSASII_REFINEMENT_ARCHIVE/<cif-key>/<sample-id>/`, or `~/GSAS-II_refinement_results/<cif-key>/<sample-id>/` by default.
- Keep the final XRD data, exact instrument file, result/source CIF, selected `.gpx`, `.lst`, report, and manifest together in the same sample folder.
- Keep `candidate_summary.json` and `report_validation.json` with every final archive.
- Do not generate, restyle, copy, or archive Rietveld figures as part of this skill.
- After verifying the final archive, delete the run's process/intermediate files from the skill staging folder. Never delete original source XRD/CIF files.
- Require the user to supply the source CIF and instrument parameter file. Never silently substitute a material-specific structure or a private instrument file.
- For an operando/in-situ series, require a CSV manifest with stable frame IDs, explicit order, pattern paths, and at least one varying experimental coordinate such as time, temperature, voltage, capacity, or state of charge.
- Support ordered one-dimensional patterns only. Do not integrate detector images, control live acquisition, smooth trajectories, or identify unknown phases automatically.

## Required references

Read these as needed:

- `references/refinement-routing.md` at the start of every request.
- `references/workflow.md` for the deterministic branched GSAS-II procedure and portable invocation.
- `references/sequential-workflow.md` for operando/in-situ and other ordered-series refinement.
- `references/sequential-manifest.md` for the required CSV schema and phase-set rules.
- `references/dialectical-review.md` for the mandatory self-debate gate before selecting a final result.
- `references/archive-policy.md` before final cleanup or moving results.

Use:

- `scripts/classify_refinement_request.py` as the mandatory read-only first gate.
- `scripts/refinement_core.py` for shared routing, manifest preflight, path validation, and category enforcement.
- `scripts/run_staged_refinement.py` to create the GPX candidates and `candidate_summary.json`.
- `scripts/run_sequential_refinement.py` to stage inputs, refine anchor frames, run forward/reverse staged sequences, and create the sequence run summary.
- `scripts/sequential_audit.py` to extract covariance-backed per-frame results, compare directions, and generate the validated sequence report.
- `scripts/select_refinement_candidate.py` to enforce the safety gate, export the final CIF, and bind selected file hashes into the candidate summary.
- `scripts/build_validate_refinement_report.py` to generate or validate the report, metric labels, and crystallographic uncertainties.
- `scripts/archive_refinement_results.py` to transactionally create the final archive and safely remove staging files.

## Category actions

- `single_pattern_refinement`: use the single-pattern workflow below.
- `sequential_refinement`: read both sequential references and use the sequential workflow.
- `independent_batch_refinement`: execute isolated single-pattern workflows with separate sample IDs, staging directories, reports, and archives. Never propagate parameters between samples.
- `detector_integration_required`: do not call GSAS-II refinement. Request calibrated one-dimensional integration and provenance.
- `plotting_handoff`: do not refine. Route an accepted GPX to `rietveld-plotting`; route heatmaps, waterfalls, and trajectory plots to an appropriate separate visualization workflow.
- `multiple_patterns_ambiguous`, `existing_project_ambiguous`, or any conflict: ask the minimum blocking question and stop.

## Single-pattern workflow

1. Require a recorded `single_pattern_refinement` classification, then inspect XRD format/range/step, CIF space group/cell/occupancy, instrument parameter file, and user sample context.
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

## Sequential workflow

1. Require a recorded `sequential_refinement` classification, then validate the manifest and every source hash. Reject duplicate frame IDs/orders, missing files, missing phase names, or file-order-only series unless the user deliberately enables that test mode.
2. Run `run_sequential_refinement.py --plan-only`. Declare whether the instrument profile is calibrated. Production sequences keep the instrument profile locked.
3. Refine start, middle, end, and requested transition-region anchors. The start and end anchors must pass convergence, SVD, shift/esd, and Rwp/Rwp-min gates before propagation.
4. Keep each phase's global cell fixed in the sequence. Represent frame-dependent cell changes with HAP `Dij/HStrain`; do not vary global `Cell` in a sequential refinement.
5. Run deterministic stages: stable background/scale/Dij first; optional sample displacement second; constrained phase fractions and justified size/microstrain third; optional atomic X/U terms last.
6. Treat phase fractions as constrained quantities. If the declared `phase_set` changes, split into scientifically justified windows or keep fractions fixed; do not apply one invalid wildcard constraint across incompatible phase sets.
7. Run forward from the first accepted anchor and reverse from the last accepted anchor. Never start both directions from the same endpoint model.
8. Require every requested stage to contain its expected variable family and all frames. A missing stage, nonconvergence, SVD failure, or frozen variable is a hard failure.
9. Read `sequential_audit.json`. Direction sensitivity, high correlation, shift/esd above 1, and robust trajectory discontinuities require review; they are not erased or relabeled as automatic success.
10. Keep the exact staged manifest, instrument file, CIFs, and patterns with `input_bundle_manifest.json`, plus both GPX files, stage snapshots, CSV/JSON results, audit, report, and validation.
11. Do not generate a figure. If requested later, route an accepted GPX and selected histogram to `rietveld-plotting`.

## Stop rules

Stop or ask the user before claiming a final structural conclusion when:

- Major peaks are unexplained by the current phase model.
- A residual audit finds large positive observed-minus-calculated peaks that are not accounted for by the current phase model, even when Rwp is numerically low.
- The imported instrument file has unusual assumptions such as `I(L2)/I(L1)=0` for a Cu Kalpha pattern and no calibrated instrument file was supplied.
- A candidate lowers Rwp through negative/nonphysical profile terms, severe SVD warnings, correlation at or above 95%, or background swallowing real peaks.
- A sequential endpoint anchor fails its gate, a stage does not refine the variables it claims to refine, or forward/reverse results show unresolved path dependence beyond the declared tolerances.
- A frame discontinuity cannot yet be distinguished between a real transition, a bad frame, and model failure. Preserve it and request scientific review; never smooth it away.
- The user wants dopant occupancy, vacancy content, or quantitative phase fractions without constraints and independent composition evidence.
- GSAS-II GUI has the same `.gpx` open; tell the user to reopen the saved project rather than saving the stale window.

## Reporting requirements

Every final report must include:

- The mandatory request classification, its input evidence, and the selected driver.
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

For a sequential result, also include:

- Manifest hash, frame count/order, independent-variable fields, phase-set declarations, and staged input-bundle hash.
- Anchor-frame metrics and gate results.
- Forward/reverse completion and per-frame Rwp, GOF, cells with covariance-derived uncertainties, phase fractions where constrained, frozen variables, and maximum correlations.
- Direction-sensitivity tolerances, continuity flags, and a clear `pass`, `review`, or `fail` distinction.
