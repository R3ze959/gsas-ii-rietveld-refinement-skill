# QPA policy

## Quantitative definition

For phase `i`, GSAS-II derives crystalline mass fraction from the phase HAP
Scale `s_i` and unit-cell mass `M_i`:

`w_i = M_i s_i / sum_j(M_j s_j)`

Use the covariance matrix to propagate uncertainty. Raw HAP Scale values are
relative unit-cell counts and are not weight fractions.

## Identifiability

A histogram Scale and every HAP Scale cannot all vary freely without an
equivalent independent constraint. Prefer a transparent reference-phase
parameterization: fix one sample-phase HAP Scale, refine the histogram Scale,
and refine the remaining sample-phase HAP Scales. Record the reference phase.
Choose an abundant, well-resolved, structurally stable reference phase where
possible. Anchoring a trace phase can leave the dominant-phase HAP Scale highly
correlated with the histogram Scale even though one parameter was formally
fixed.

When phase abundance is not known in advance, do not infer the reference phase
from a hidden target or expected answer. Run both defensible anchors from a
declared start grid, reject failed or clearly inferior fits, and choose among
GOF-equivalent paths using conditioning and path stability. Preserve every
candidate summary so the decision is auditable.

Do not impose `sum(HAP Scale)=1` and label the result a mass-fraction
constraint. Different phases have different unit-cell masses.

## Model preflight

The declared phase set is part of the quantitative model, not a free-text
assumption. Record it as `verified`, `provisional` or `unknown` before fitting.
Unknown blocks QPA. Provisional is development-only and remains `review`; it is
not valid for a held-out prediction. Verified requires an auditable evidence
identifier and does not mean that residual peaks may be ignored. Classify a
set as provisional only after a concrete qualitative screen assigns all known
major peaks; an unassigned major peak makes the phase set unknown. Hash a local
report/frozen list into the protocol, or record a syntactically valid DOI.

Audit every source CIF independently of the GSAS-II import. Compare the
unit-cell composition from atom multiplicity times occupancy (or formula times
Z when needed) with GSAS-II `NoAtoms`, and compare the independently calculated
cell mass with the imported mass. A mismatch or an incompatible setting message
is a hard failure because the scale-to-mass conversion would otherwise be
wrong even when the profile appears to refine.

For real patterns, calibrate and then lock the instrumental peak profile before
estimating composition. A standard-pattern calibration must keep the standard
cell fixed and compare staged profile releases using convergence, SVD,
correlation and positive-width gates; Rwp alone is not a safe selector.
Production QPA must bind the passed calibration summary and its selected-profile
SHA-256 to the exact instrument file; a user-entered `calibrated` label alone
is not calibration evidence.

If matched pure-phase patterns from the same measurement batch are available,
they may be used to refine isotropic displacement parameters when every value
is positive, physically bounded and supported by a nonsingular, well-
conditioned fit. Freeze the accepted structure for the mixture. Do not assume
that Size or Mustrain measured on a pure powder transfers exactly to a mixture.
Use calibrated-profile/locked-sample-broadening as the primary QPA model and a
transferred-broadening fit only as systematic sensitivity unless independent
specimen evidence justifies the transfer.

Treat anchor paths with numerically indistinguishable GOF as competing models.
Rank them by conditioning before shift and stable name; a tiny GOF decrease
must not select a trace-phase anchor with a near-singular histogram/HAP scale
pair. The default real-pattern relative GOF window is `1e-5`; retain all paths
so this tolerance can be audited.

Candidate selection first minimizes scientific hard failures and only then
uses GOF and conditioning. A fit containing a major residual, invalid phase
fraction or failed covariance result cannot win merely because its Rwp/GOF is
slightly lower.

## Cells and deterministic reruns

Keep every phase cell locked in the primary composition candidates. A
controlled sensitivity branch may release only one abundant phase at a time.
Accept a cell step only when GSAS-II actually varied the expected cell-variable
family, convergence and SVD gates pass, correlation is safe, relative cell
volume change stays within the frozen bound and the relative GOF improvement
meets the frozen minimum. Re-lock accepted cells before refining phase scales
and sample Shift. Preserve accepted and rejected branches.

After model selection, rebuild the same final model in fresh GPX projects from
the exact refined HAP scales, histogram Scale and sample Shift. Use at least
three total records by default. A failed rerun, missing covariance-backed mass
fraction or per-phase range greater than 0.005 (0.5 wt%) is a hard numerical
repeatability failure. This tests deterministic reconstruction; it does not
replace experimental replication.

## Phase roles

Assign each modeled phase one role: `sample`, `hardware` or
`internal_standard`. The profile and all-scale audit retain every role, but the
reported sample QPA is renormalized only over `sample` phases with covariance
propagation from their HAP scales. Never include a cell window, collector or
holder peak in the sample total. An `internal_standard` role records and models
the standard but does not by itself establish amorphous content. Require
exactly one standard, the known standard fraction after addition, its weighing
uncertainty and a hash-bound addition record. Normalize the refined standard
over crystalline sample phases plus standard, excluding hardware, and report
the calculated amorphous fraction on the original-sample basis.

## Common-problem sensitivity

Keep preferred orientation locked off in the primary model. A schema-validated,
hash-bound March-Dollase sensitivity may release one phase/axis at a time from
the exact selected state. The evidence axis must exactly match the CLI axis.
Require a physical ratio, convergence, no SVD truncation and safe correlation.
Never promote a texture model solely because Rwp or GOF is lower; a material
safe branch remains `review` until it is declared and rerun as the primary
model.

Treat microabsorption as external model sensitivity because this driver does
not refine a general particle-geometry correction. Use explicit true-mass
multiplier intervals covering every non-hardware phase and propagate the
worst-case normalized fraction shift. Do not disguise assumed density,
absorption or particle-size inputs as fitted GSAS-II parameters.

Classify a trace phase from the conservative combined uncertainty when
available. A 3-sigma detection and 10-sigma quantification screen is not a
formal LOD/LOQ; retain review unless spike-in recovery or a separately frozen
profile-likelihood validation supports the claim.

For doped phases, use predeclared, chemically valid CIF variants under
identical non-target settings. Do not freely refine occupancy with scale,
displacement and texture terms to infer composition. GOF-equivalent variants
remain indistinguishable and require independent composition or site evidence.
Even a unique best profile model remains `review` for a dopant content/site
claim: the grid ranks models but does not turn profile agreement into chemical
proof.

## Scientific gates

- Exclude non-sample hardware phases from the reported sample normalization.
- Require a known, uncertainty-bearing internal-standard addition for
  amorphous-content measurement.
- Audit preferred orientation before trusting affected phase fractions.
- Audit microabsorption when phases have substantially different absorption or
  particle-size behavior.
- Retain covariance-backed standard uncertainties. Missing uncertainties are a
  review condition, not zero uncertainty.
- A covariance ESD describes local statistical precision under one model. When
  an admissible broadening sensitivity model changes phase fractions, report
  that spread separately and combine it conservatively with the ESD; do not
  relabel the combined value as a pure standard uncertainty.
- Correlation of 95% or greater, negative fractions, severe overlap, unstable
  corrections or path sensitivity require `review` or `fail`.
- A low Rwp does not override unexplained peaks or nonphysical parameters.
- A positive local residual at least 10% of the pattern maximum is a hard
  profile/phase-model failure whether or not a calculated peak exists there.
- In multi-start tests, distinguish a trajectory-wide maximum shift/esd from a
  final-cycle convergence statistic; label the scope and do not interchange
  them.
