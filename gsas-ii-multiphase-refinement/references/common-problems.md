# Common multiphase-QPA problems

Use this reference only after the phase set, instrument profile, CIF imports
and primary multi-anchor QPA have passed preflight. All evidence files are
parsed as JSON, validated against the exact declared branch and then hashed
into `protocol_manifest.json`. A generic note, a mismatched axis/interval or a
file with `status` other than `pass` is rejected before GSAS-II starts.

## Preferred orientation

Choose one policy before the run:

- `not_assessed`: retain `preferred_orientation_not_assessed` as `review`.
- `assessed_negligible`: provide a specimen/preparation evidence file showing
  why texture is negligible.
- `sensitivity`: provide the evidence file and one justified `h,k,l` axis for
  every phase that requires testing.

Evidence contract and matching CLI example:

```json
{
  "schema_version": 1,
  "kind": "preferred_orientation_assessment",
  "status": "pass",
  "basis": "plate-like morphology and declared cleavage axis",
  "policy": "sensitivity",
  "conclusion": "sensitivity_defined",
  "phase_axes": {"layered_phase": [0, 0, 1]}
}
```

```bash
--preferred-orientation-policy sensitivity \
--preferred-orientation-axis layered_phase=0,0,1 \
--preferred-orientation-evidence-file texture_assessment.json
```

The driver starts each March-Dollase trial from the exact selected scale,
histogram Scale and Shift state. It tests one sample phase at a time, requires
convergence, no SVD truncation, correlation below the frozen limit and a
bounded positive March-Dollase ratio. It never promotes a trial automatically.
A material safe GOF improvement or phase-fraction shift is retained as
`review` and requires a separately declared primary-model rerun.

Do not guess an axis from whichever option gives the lowest Rwp. Bind the axis
to morphology, known cleavage/layering or an independently declared
crystallographic hypothesis.

For `assessed_negligible`, use the same envelope with
`"policy": "assessed_negligible"` and `"conclusion": "negligible"`; omit
`phase_axes`. The nonempty `basis` must describe the specimen/preparation
assessment, not merely repeat the conclusion.

## Microabsorption

GSAS-II does not refine a general Brindley microabsorption correction in this
driver. Use one of these policies:

- `not_assessed`: retain `microabsorption_not_assessed` as `review`.
- `assessed_negligible`: provide evidence covering phase absorption contrast
  and particle-size preparation.
- `sensitivity`: provide a direct true-mass multiplier interval for every
  non-hardware phase, including an internal standard.

Evidence contract and matching CLI example:

```json
{
  "schema_version": 1,
  "kind": "microabsorption_assessment",
  "status": "pass",
  "basis": "absorption contrast and particle-size bounds",
  "policy": "sensitivity",
  "conclusion": "sensitivity_defined",
  "multiplier_definition": "true crystalline mass contribution = Rietveld contribution * multiplier",
  "phase_intervals": {
    "phase_a": [0.94, 1.06],
    "phase_b": [0.98, 1.02],
    "standard": [1.00, 1.00]
  }
}
```

```bash
--microabsorption-policy sensitivity \
--microabsorption-multiplier phase_a=0.94,1.06 \
--microabsorption-multiplier phase_b=0.98,1.02 \
--microabsorption-multiplier standard=1.00,1.00 \
--microabsorption-evidence-file absorption_particle_size.json
```

The interval definition is explicit:

`true crystalline mass contribution = Rietveld contribution * multiplier`

The driver calculates worst-case normalized fraction intervals and adds the
maximum sample-fraction shift to the model-uncertainty budget. It does not
overwrite the GSAS-II phase fractions or claim that an assumed particle
geometry was refined.

For `assessed_negligible`, use `"conclusion": "negligible"` and document both
absorption contrast and particle-size preparation in `basis`.

## Internal-standard amorphous content

Model exactly one phase as `internal_standard`. Supply the known standard mass
fraction after addition, its standard uncertainty and the weighing record:

```json
{
  "schema_version": 1,
  "kind": "internal_standard_addition",
  "status": "pass",
  "basis": "post_addition_total_mass",
  "conclusion": "addition_record_verified",
  "standard_phase": "standard",
  "added_fraction_after_mixing": 0.2000,
  "added_fraction_esd": 0.0005
}
```

```bash
--phase standard=standard.cif \
--phase-role standard=internal_standard \
--internal-standard-added-fraction 0.2000 \
--internal-standard-added-fraction-esd 0.0005 \
--internal-standard-evidence-file standard_addition_record.json
```

Let `W_added` be standard mass divided by total mass after addition and
`R_refined` be the refined standard fraction normalized over crystalline
sample phases plus the standard, excluding hardware. Report the original-
sample amorphous fraction:

`A = (1 - W_added / R_refined) / (1 - W_added)`

Propagate covariance ESD from `R_refined` and weighing uncertainty from
`W_added`. A negative or greater-than-one value is a hard inconsistency, not a
quantity to clip silently. Missing weighing uncertainty forces `review`.

## Trace phases

The driver screens every sample phase at or below the declared trace threshold
(default 5 wt%):

- signal/uncertainty below 3: `not_detected_statistically`;
- from 3 to below 10: `detected_not_quantifiable`;
- at least 10: `quantified_statistically`.

Prefer the conservative combined uncertainty. Falling back to incomplete
model uncertainty or covariance ESD alone forces `review`. These classes are
not a validated instrumental LOD/LOQ. Use spike-in recovery or a frozen
profile-likelihood protocol for a formal detection/quantification claim.

## Doped phases and constrained CIF grids

Do not release free occupancies together with phase Scale, displacement
parameters and preferred orientation to infer dopant content. Prepare a small,
predeclared grid of chemically and crystallographically valid CIF variants,
run each variant through the same `run_multiphase_qpa.py` settings, then score
the completed summaries:

```json
{
  "schema_version": 1,
  "kind": "constrained_model_grid",
  "status": "pass",
  "basis": "predeclared composition and site hypotheses",
  "target_phase": "doped_phase",
  "variant_labels": ["x0.05_siteA", "x0.05_siteB"],
  "claim_scope": "model_comparison_only"
}
```

```bash
python scripts/score_constrained_model_grid.py \
  --target-phase doped_phase \
  --evidence-file composition_and_site_hypotheses.json \
  --variant x0.05_siteA=run-x005-a/qpa_summary.json \
  --variant x0.05_siteB=run-x005-b/qpa_summary.json \
  --output constrained_model_grid.json
```

The scorer verifies that pattern, instrument, non-target phase hashes and all
frozen settings are identical. It rejects incomplete QPA summaries, generic
evidence, duplicate CIF hashes and undeclared variants. It minimizes hard
failures first, treats GOF-equivalent variants as competitive and ranks those
by conditioning. The selected label is only the preferred profile model:
every scientifically clean grid remains `review` for dopant content/site
claims, and multiple competitive variants add a second indistinguishability
flag.
