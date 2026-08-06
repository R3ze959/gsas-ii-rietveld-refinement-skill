# Training, validation and held-out test plan

Keep the three datasets separated. Do not tune thresholds or code using the
held-out IUCr answers.

## Stage 1 - training: GSAS-II CuCr2O4/CuO Simulation

Purpose: implement and debug the identifiable two-phase scale model and
covariance-backed mass-fraction extraction.

- Generate known CuO mass fractions of 1%, 10%, 50% and 90% with fixed CIFs
  and instrument profile.
- Use high-count simulated patterns so the test measures implementation error
  rather than minority-phase counting statistics.
- Use one common mass-weighted specimen basis across all compositions so the
  simulation does not change total sample amount when the phase ratio changes.
- Try both fixed-HAP reference phases from declared low/mid/high starts. Select
  among GOF-equivalent candidates by conditioning without consulting the known
  target. Record all candidates and the selected path.
- Require convergence, no SVD truncation, competitive-path agreement, maximum
  correlation below 95% and absolute fraction error at most 0.2 wt%.
- Treat the reported GSAS-II `Max shft/sig` as a trajectory maximum in this
  deliberately distant-start harness, not as a final-cycle shift.
- Freeze only after at least 20 seeds per composition and report bias, RMSE,
  maximum error, 1-sigma/2-sigma coverage and reference-phase selection counts.

Allowed use: code development and threshold debugging.

## Stage 2 - validation: GSAS-II SeqTut

Purpose: validate fixed-phase-set multiphase sequential control after Stage 1
is frozen.

- Use all 17 frames in manifest order.
- Run forward and reverse from independent endpoint anchors.
- Verify complete variable families, covariance-backed phase fractions,
  source/staged hashes and deterministic exported results.
- Treat direction sensitivity, correlation at or above 95%, final-cycle
  shift/esd above 1 or a discontinuity as `review`. Do not silently substitute
  a trajectory-wide maximum for a final-cycle quantity.
- Classify persistent residuals using the net modeled profile
  `Ycalc - Ybackground`. A large mismatch at an existing Bragg peak is
  `review`; an equally large residual without modeled peak intensity remains a
  hard missing-model failure.
- Stage validation may be `accepted_with_review` only when both directions are
  complete, all hard failures and covariance-uncertainty gaps are zero, the
  instrument is declared calibrated, report integrity passes, and every
  remaining review item is retained. This validates the workflow, not a
  publication-ready structure model.

If Stage 2 causes a code or threshold change, return to Stage 1, rerun it, and
version the frozen implementation before validating again.

Validation implementation note: constrained sequential phase fractions are
stored by GSAS-II as dependent `WgtFrac` parameters. Export their values and
standard uncertainties from `depParmDict` before falling back to an
unconstrained mass-fraction calculation. A numeric fraction without this
covariance path remains `review`.

Materialize the decision with `scripts/validate_sequential_stage.py`; do not
manually relabel the underlying sequential audit.

## Stage 3 - IUCr CPD QPA Round Robin

Purpose: test real-pattern QPA and known failure modes without tuning on the
reference answers.

- Freeze code, thresholds, phase models and correction policy before reading
  measured/weighed reference values.
- Test the simple mixture first, then preferred-orientation, amorphous-content
  and microabsorption cases.
- Require an internal standard for an amorphous-content claim.
- Report phase-wise absolute error, uncertainty coverage, minor-phase behavior,
  correlations, residual peaks and model warnings.
- Keep the files local because redistribution permission is unclear.

Failure on this stage is a scientific finding. Do not repair a held-out case
and continue calling the same data an untouched test set.

### Recorded outcome and current boundary

The original CPD-1G prediction was archived and hashed before the reference
answers were opened. It predicted 34.0353 wt% corundum, 32.9020 wt% fluorite
and 33.0627 wt% zincite. Against the weighed 31.37/34.42/34.21 wt% values, its
RMSE was 1.8907 wt% and maximum absolute error was 2.6653 wt%. This is a failed
blind test; thresholds supplied after unblinding are diagnostic and cannot be
retroactively called predeclared.

Diagnosis showed that selecting lower Rwp by transferring pure-powder
broadening into the mixture worsened composition. The corrected development
policy refines physically gated pure-reference Uiso values, freezes those
structures, keeps sample broadening locked in the primary mixture model and
uses transferred broadening only as systematic sensitivity. On CPD-1G this
post-unblinding policy gave RMSE 0.1860 wt% and maximum absolute error 0.2551
wt%, with all three errors covered by the conservative statistical-plus-model
uncertainty. This validates the repair on known data, not a new blind success.

The same frozen post-unblinding structure/profile model was then checked on
simple mixtures CPD-1A through CPD-1H. All eight met the proposed next-test
accuracy gates (per-pattern RMSE at most 0.75 wt% and maximum absolute phase
error at most 1.0 wt%); pooled phase-wise RMSE was 0.2493 wt% and the largest
absolute error was 0.6117 wt%. Results remain `review`, rather than `pass`,
because modeled Bragg-peak residuals persist. A trace-anchor case also led to
the answer-independent competitive-GOF fix: paths within relative GOF `1e-5`
are now compared by conditioning, reducing CPD-1B maximum correlation from
98.77% to 72.57% without materially changing its phase fractions.

Because the IUCr answers are now known, CPD-1A through CPD-1H are development
and regression data. A future independent dataset must freeze the following
gates before prediction: maximum absolute phase error at most 1.0 wt%, pooled
or per-pattern RMSE at most 0.75 wt% as declared in the protocol, conservative
uncertainty coverage for every phase, no SVD failure, and explicit retention
of all correlation and residual warnings.

### Post-blind skill repair boundary

A later independent real-pattern attempt exposed implementation defects before
scientific accuracy could be judged: source CIFs could be imported into a
different GSAS-II setting/composition without an independent mass check;
candidate ranking did not prioritize profile hard failures; cells lacked a
strict one-phase-at-a-time promotion gate; and repeated fresh-project runs did
not reconstruct the exact selected scale state.

The development implementation now blocks CIF/import mismatches, requires a
declared phase-set gate, ranks by hard-failure count before GOF, applies a
controlled abundant-phase cell branch and repeats the selected model from its
exact HAP/histogram/Shift state. A two-phase post-unblinding GSAS-II smoke test
gave a maximum three-run phase-fraction range below `8e-7`, while the
deliberately incomplete phase set still failed on major residuals. This verifies
the new control logic only. It is not a replacement blind-test success, and the
skill must not be declared scientifically validated until a new frozen
held-out dataset passes.
