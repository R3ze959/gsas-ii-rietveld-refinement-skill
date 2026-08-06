---
name: gsas-ii-multiphase-refinement
description: Beta workflow to classify and run GSAS-II multiphase powder Rietveld refinement for two or more declared crystalline phases, quantitative phase analysis (QPA), preferred-orientation and microabsorption sensitivity, trace-phase classification, internal-standard amorphous quantification, constrained dopant-model grids, and declared-phase sequential or operando series. Use when powder XRD contains multiple candidate phases, when the user requests phase fractions or impurity quantification, or when phases appear or disappear across an ordered series. Do not use for unknown-phase identification, single-phase refinement, detector-image integration, or plotting.
---

# GSAS-II Multiphase Refinement (Beta)

**Release status: Beta.** This skill is suitable for declared-phase research
workflows and reproducible evaluation, but it is not a substitute for expert
phase identification or independent validation. Preserve every `review` and
`fail` gate, and do not market a completed run as publication-ready by default.

Treat phase fractions as scientific quantities with covariance-backed
uncertainties, not normalized raw scale factors. Use real GSAS-II for every
numerical result and keep plotting as a downstream handoff.

## Route first

Classify the request before creating a GPX:

- `multiphase_single`: one pattern and at least two declared phase models.
- `multiphase_qpa`: one pattern with quantitative phase fractions requested.
- `multiphase_sequential_constant_set`: an ordered manifest where the same
  sample phases remain active in every frame.
- `multiphase_sequential_changing_set`: an ordered manifest split into stable
  phase-set windows.
- `unknown_phase`: important unexplained peaks without candidate structures;
  stop and request phase identification.

Route ordinary single-phase work to `gsas-ii-rietveld-refinement`. Route only
accepted GPX files to `rietveld-plotting` when a figure is separately requested.

The sequential routes have an explicit companion-skill dependency: run
`../gsas-ii-rietveld-refinement/scripts/run_sequential_refinement.py` with one
`--cif` and matching `--phase-name` per modeled phase, then apply this skill's
`scripts/validate_sequential_stage.py`. Confirm that the companion script is
present before promising sequential execution; if it is absent, stop and ask
for `gsas-ii-rietveld-refinement` to be installed from the same release.

For a constant phase set, use `--phase-fractions refine` and declare the same
phase set in every manifest row. For a changing set, place explicit transition
anchors at every boundary and keep each refinement segment phase-set stable;
never carry a refined phase fraction through a frame where that phase is
declared absent. The companion driver intentionally rejects a varying phase
set inside one phase-fraction-refining segment.

## Mandatory QPA rules

1. Declare the phase-set status before refinement. `unknown` blocks QPA;
   `provisional` forces `review` and cannot be used for a held-out test;
   `verified` requires an auditable basis such as a DOI, phase-identification
   report or independently frozen candidate list. Use `provisional` only when
   a concrete screened candidate list exists and no known major peak remains
   unassigned; otherwise use `unknown`.
2. Require a CIF or otherwise traceable structural model for every quantified
   crystalline phase. Run `scripts/audit_phase_models.py` before numerical
   fitting and stop if the independently parsed CIF composition or cell mass
   disagrees with the GSAS-II import, or if GSAS-II reports an incompatible
   space-group setting.
3. Require a calibrated instrument parameter file for production work. Keep
   the instrumental profile locked during composition fitting. A `calibrated`
   declaration requires `calibration_summary.json`; its pass status and
   selected-profile SHA-256 must match the supplied instrument file.
4. Resolve the sample-position term from the imported GSAS-II geometry rather
   than assuming every histogram contains `Shift`. Prefer the available order
   `Shift`, `DisplaceX`, `DisplaceY`, refine at most one after scale/background,
   and record its exact name and value. If none is available, skip that stage;
   never create a nonexistent parameter.
5. Identify sample phases separately from cell/window/collector/hardware
   phases with `--phase-role`. Exclude `hardware` and `internal_standard` roles
   from the reported sample-phase normalization while retaining them in the
   profile model.
6. Avoid scale degeneracy. Fix one well-conditioned reference-phase HAP Scale
   and refine the histogram Scale plus all other modeled-phase HAP Scales, or
   use an equivalent independently justified identifiable parameterization.
   Prefer an abundant, well-resolved, structurally stable phase as reference;
   do not mechanically anchor a trace or poorly resolved phase.
7. Compute weight fractions with GSAS-II `ComputeMassFracs()` or
   `calcMassFracs()` and retain covariance-derived uncertainties. Never report
   normalized HAP Scale values as weight fractions.
8. Keep cells locked for the primary QPA. If cell sensitivity is requested,
   release one abundant phase at a time and accept it only when the expected
   cell variable family is present, convergence and SVD gates pass, correlation
   is below the limit, cell-volume change is bounded and GOF improves by the
   frozen minimum. Lock accepted cells again before recomputing composition.
9. Rebuild the selected final model at least twice from the exact selected HAP
   scales, histogram Scale and sample Shift. A failed rerun or a per-phase range
   above 0.5 wt% is a hard failure.
10. Require exactly one modeled internal standard, a known post-addition mass
   fraction, its weighing uncertainty and a schema-validated, hash-bound
   evidence file before claiming amorphous content. Report the result on the
   original-sample basis.
11. Mark the result `review` for missing uncertainties, correlation at or above
   95%, negative fractions, unresolved preferred orientation or
   microabsorption, severe overlap, or an uncalibrated profile.
12. Mark the result `fail` for nonconvergence, SVD failure, failed CIF import
   integrity, invalid phase-set constraints, repeatability failure, or any
   positive residual peak at least 10% of the pattern maximum. Do not let a
   lower-GOF candidate outrank a candidate with fewer hard failures.

13. Never freely refine dopant occupancy to infer composition. Refine frozen,
   schema-validated, evidence-bound CIF variants independently and compare them with
   `scripts/score_constrained_model_grid.py`; indistinguishable models remain
   `review`, and even a unique best profile model cannot by itself establish
   dopant content or site occupancy.

Read `references/qpa-policy.md` before reporting phase fractions. Read
`references/common-problems.md` before enabling preferred orientation,
microabsorption, amorphous content, trace-phase claims or a constrained model
grid. Read
`references/training-validation-plan.md` when developing or regression-testing
this skill.

## Deterministic drivers

Calibrate the instrumental profile from a suitable standard before a real QPA
run. Keep the standard cell fixed so Zero and the cell do not compensate each
other:

```bash
python scripts/calibrate_instrument_profile.py \
  --standard-pattern standard.xye \
  --standard-cif standard.cif \
  --seed-instrument seed.instprm \
  --output-dir calibration-run
```

Then run every declared sample phase from every anchor and a composition start
grid. Pure-pattern references are optional, but when present the driver uses
them to refine physically gated isotropic displacement parameters before
freezing those structures in the mixture:

```bash
python scripts/run_multiphase_qpa.py \
  --sample-id sample-01 \
  --pattern mixture.xye \
  --instrument calibration-run/calibrated.instprm \
  --phase phase_a=phase_a.cif \
  --phase phase_b=phase_b.cif \
  --phase cell_window=cell_window.cif \
  --phase-role cell_window=hardware \
  --pure-reference phase_a=phase_a_pure.xye \
  --pure-reference phase_b=phase_b_pure.xye \
  --phase-set-status verified \
  --phase-set-evidence-kind report \
  --phase-set-evidence-file phase-identification-report.json \
  --instrument-profile-status calibrated \
  --instrument-calibration-summary calibration-run/calibration_summary.json \
  --broadening-policy ensemble \
  --preferred-orientation-policy not_assessed \
  --microabsorption-policy not_assessed \
  --cell-policy controlled \
  --repeatability-runs 3 \
  --output-dir qpa-run
```

For a development-only run with an incomplete candidate list, use
`--phase-set-status provisional`; the output must remain `review`. Never label
an unknown phase set as provisional merely to bypass the preflight.
For `verified`, a DOI must match `10.xxxx/...`; a report or frozen phase list
must be supplied as a file whose SHA-256 is written into the protocol.

`ensemble` keeps calibrated instrumental broadening with sample broadening
locked as the primary composition model. It transfers pure-powder Size or
Mustrain only in a separate sensitivity fit, then combines that fraction
spread with the covariance ESD. Never select the transferred-broadening model
merely because its Rwp is lower: a pure reference and a mixture can have
different particle-size or strain broadening.

`controlled` keeps cells locked in every primary anchor/start candidate. It
then tests abundant-phase cells one at a time and may promote only a fully
gated branch. Use `--cell-policy sensitivity` to record the same comparison
without allowing promotion, or `locked` when cell changes are outside scope.
The final exact-state reruns are a numerical reproducibility audit, not
independent experimental replicates.

`not_assessed` preferred orientation or microabsorption forces `review`.
`assessed_negligible` requires a schema-validated, hash-bound
specimen/preparation assessment. `sensitivity` requires a matching JSON
contract plus explicit phase axes or true-mass multiplier intervals. Generic
notes, contradictory status fields and numerical declarations that differ
from the CLI are rejected before refinement. March-Dollase trials and
microabsorption interval corrections never replace the primary model
automatically. Their phase-fraction spreads enter the conservative uncertainty
budget.

For a known internal-standard addition, assign that phase
`role=internal_standard` and provide the added fraction, its ESD and weighing
record. The driver calculates the original-sample amorphous fraction and
propagates both standard-refinement and weighing uncertainties. A role label
alone is not an amorphous measurement.

Treat the automatic 3-sigma/10-sigma trace classification as an uncertainty
screen, not a validated instrumental LOD/LOQ. Formal LOD/LOQ claims still need
spike-in or profile-likelihood validation.

The real-pattern driver treats anchor paths within relative GOF `1e-5` as
numerically competitive before ranking by correlation and shift. This prevents
a negligible GOF improvement from selecting a nearly singular trace-phase
anchor. Candidate selection first minimizes scientific hard failures, so a
major residual cannot be traded for a lower GOF. Preserve
`phase_model_audit.json`, `candidate_summary.json`, `protocol_manifest.json`,
`qpa_summary.json`, the selected GPX and `prediction_archive.json`.

After a genuinely blinded result has been archived and hashed, score it in a
separate step:

```bash
python scripts/score_qpa_prediction.py \
  --prediction qpa-run/prediction_archive.json \
  --reference phase_a=50.0 \
  --reference phase_b=50.0 \
  --reference-label weighed_reference \
  --reference-source reference_record \
  --output qpa-run/unblinded_score.json
```

The scorer cannot prove that thresholds were frozen before prediction; verify
that against the hash-bound protocol. Once answers have been read, all further
runs on that dataset are development or post-unblinding validation, not
held-out tests.

The two-phase simulation harness remains the deterministic Scale/QPA
regression test:

```bash
python scripts/run_two_phase_qpa_training.py \
  --phase-a-cif CuCr2O4.cif \
  --phase-b-cif CuO.cif \
  --instrument BT1.prm \
  --replicates 20 \
  --starting-fractions 0.05,0.50,0.95 \
  --output-dir /path/to/new-run \
  --gsasii-path /path/to/GSAS-II
```

It keeps a constant mass-weighted specimen basis, tries both possible
reference phases from declared low/mid/high starting fractions, and selects
only from statistically competitive paths using convergence, GOF and
conditioning. The target composition is never consulted during candidate
selection; it is used only to generate and score the simulated case. The
harness calls real GSAS-II mass-fraction computation and writes every candidate
summary plus aggregate bias, RMSE and uncertainty coverage to
`training_summary.json`. It is a scale/QPA unit test, not evidence that
a structure model, preferred-orientation model, microabsorption correction, or
unknown-phase search is correct.

For fixed-phase-set or correctly segmented changing-set sequential validation,
retain the companion refinement driver's `review`/`fail` status and create a
separate, hash-bound stage decision:

```bash
python scripts/validate_sequential_stage.py \
  --run-dir /path/to/sequential-run \
  --audit-dir /path/to/re-audited-results \
  --output /path/to/stage_scientific_acceptance.json
```

`accepted_with_review` is allowed only when the run is complete in both
directions, contains no hard failure or uncertainty gap, uses a declared
calibrated profile, and passes report integrity. It never means that remaining
shift/esd, path-dependence, cell-correlation, or modeled-peak residual warnings
have disappeared.

Do not use GSAS-II's trajectory-wide `Max shft/sig` as if it were a final-cycle
shift for deliberately distant multi-start candidates. Retain it in the audit,
but gate this harness on convergence, SVD, competitive-path agreement,
correlation, fraction validity and known-truth error.

Current evidence supports declared crystalline-phase QPA with a calibrated
profile. Real-pattern results remain `review` when modeled-peak residuals,
preferred orientation, microabsorption, trace-phase uncertainty or model
sensitivity remain. The skill does not identify unknown phases and does not
infer dopant content/site occupancy from an unconstrained refinement. A
validated evidence contract proves that the declared file matches the frozen
protocol; it does not independently prove that the experimental statements in
`basis` are true. Preserve the underlying specimen, microscopy, absorption or
weighing records for scientific review.

## Reporting

Report:

- route, phase roles, source hashes, GSAS-II path/version and instrument status;
- refinement stages, exactly which scale was fixed, and the geometry-specific
  sample-position parameter that was refined or skipped;
- Rwp, GOF, convergence, SVD count, maximum shift/esd and maximum correlation;
- phase mass fractions with covariance-derived uncertainties;
- phase-set evidence, CIF/GSAS-II import audit and exact-state repeatability;
- controlled-cell decisions and every rejected cell branch;
- preferred-orientation, microabsorption and constrained-model sensitivity;
- trace-phase classification and internal-standard amorphous content when valid;
- all `pass`, `review`, and `fail` gates;
- limitations and whether an internal standard was present;
- an explicit statement that no figure was generated.
