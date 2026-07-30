# Operando and Sequential Refinement Plan

## Decision

Add operando support to `gsas-ii-rietveld-refinement` as an independent
sequential mode. Keep a single top-level refinement skill because single-pattern
and sequential refinements share GSAS-II invocation, input validation,
instrument handling, structural-model checks, reporting, and archival rules.

Keep the implementation isolated so it can become a separate skill later if it
expands into detector-image integration, broad vendor-format conversion,
automatic phase discovery, or live acquisition control.

Rietveld plotting remains a separate skill. The refinement workflow must not
generate figures.

## Repository baseline

Before implementing operando support, reconcile the published repository with
the currently installed skills:

- The installed refinement skill contains a newer deterministic staged driver,
  candidate selection, report validation, residual audit, and transactional
  archival logic that are not all present in this repository.
- Remove the obsolete plotting script from the refinement skill.
- Import the standalone `rietveld-plotting` skill as its own top-level folder.
- Preserve the existing public-release rule: do not include personal absolute
  paths, private instrument files, experimental raw data, or local caches.

## Version 1 scope

Support an ordered series of already integrated one-dimensional powder
diffraction patterns. Require:

- one or more phase CIF files;
- one calibrated or explicitly declared uncalibrated instrument parameter file;
- a sequence manifest;
- frame ordering and at least one independent variable such as time,
  temperature, voltage, capacity, or state of charge.

Do not support detector-image integration, live beamline acquisition, automatic
unknown-phase identification, or figure generation in version 1.

## Proposed resources

```text
gsas-ii-rietveld-refinement/
├── SKILL.md
├── scripts/
│   ├── refinement_core.py
│   ├── run_staged_refinement.py
│   ├── run_sequential_refinement.py
│   └── sequential_audit.py
└── references/
    ├── workflow.md
    ├── sequential-workflow.md
    └── sequential-manifest.md
```

The manifest should include, at minimum:

```text
frame_id,pattern_path,order,time_s,temperature_K,voltage_V,
current_mA,capacity_mAh,state_of_charge,phase_set
```

Allow blank metadata fields, but never infer missing experimental metadata
silently.

## Deterministic workflow

1. Validate all pattern paths, hashes, point counts, ranges, step sizes,
   instrument parameters, CIF files, frame order, and metadata.
2. Select representative anchor frames at the start, middle, end, and any
   user-declared transition region.
3. Refine anchor frames conservatively with the existing single-pattern
   branching logic.
4. Build a multi-histogram GPX and copy validated histogram and HAP settings.
5. Lock the calibrated instrument profile. For uncalibrated profiles, forbid
   free U/V/W in the production sequence.
6. Refine scale, background, and justified sample displacement first.
7. Track histogram-dependent unit-cell changes with HAP `Dij/HStrain` terms
   rather than independently varying one global phase cell for every frame.
8. Release phase fractions and sample broadening only after geometry is stable.
9. Use warm-start sequential refinement, then repeat in reverse order to
   measure path dependence.
10. Audit every frame and the full trajectory before accepting the sequence.

The core GSASIIscriptable controls are:

```python
gpx.set_Controls("sequential", gpx.histograms())
gpx.set_Controls("cycles", 10)
gpx.set_Controls("seqCopy", True)
gpx.refine()
seq = gpx.seqref()
```

## Required audits

- convergence and frozen/out-of-range variables per frame;
- Rwp, Rp, GOF, covariance-derived uncertainties, and maximum correlations;
- unexplained positive residual peaks;
- nonphysical profile, size, strain, occupancy, or phase-fraction values;
- phase-fraction constraints and phase appearance/disappearance rules;
- continuity and outlier checks for cell, volume, phase fraction, and profile
  parameters;
- forward-versus-reverse sequential sensitivity;
- deterministic rerun consistency;
- graceful isolation of a malformed or failed frame without corrupting other
  results.

Do not smooth refinement results to make trajectories appear more continuous.
Flag discontinuities for scientific review.

## Outputs

Retain:

- the input manifest with hashes;
- anchor-frame candidate summaries;
- forward and reverse GPX projects;
- `sequential_results.json`;
- `sequential_results.csv`;
- `sequential_audit.json`;
- canonical report and passing validation file;
- exact CIF and instrument files.

Do not archive third-party training data in this repository.

## Public validation data

Use a staged validation ladder:

1. GSAS-II Sequential Refinement tutorial: 17 temperature frames, CuCr2O4 and
   CuO phases, CIF files, instrument file, and `.fxye` patterns.
   <https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/SequentialTutorial.htm>
2. Si/graphite operando XRD replication dataset, DOI 10.18710/MXJZMR. It
   provides public one-dimensional patterns, raw diffraction files,
   electrochemistry, TOPAS inputs, and reference results under CC0.
   <https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/MXJZMR>
3. Li-S operando XRD dataset, DOI 10.5281/zenodo.3514967. It contains 131
   one-dimensional patterns and operando electrochemistry under CC BY 4.0, but
   no bundled CIF or instrument file. Use it for batch and metadata tests, not
   as the first full Rietveld reference.
   <https://zenodo.org/records/3514967>

Store only download instructions, source citations, checksums, and small
repository-owned fixtures. Keep third-party data licenses separate from the
repository code license.

## Acceptance gates

- The official 17-frame tutorial completes twice with reproducible output.
- Forward and reverse results agree within declared tolerances or are rejected
  as path dependent.
- The two-phase fraction constraint remains valid for every applicable frame.
- Cell parameters and uncertainties are exported from sequential covariance
  results, not reconstructed from rounded report text.
- A deliberately malformed frame is isolated and reported.
- The real operando subset imports, preserves frame order, and aligns
  diffraction frames with experimental metadata.
- No figure is generated by the refinement skill.

## Implementation status

The version-1 driver, manifest schema, staged workflow, forward/reverse audit,
input bundle, and report validation are implemented. The official 17-frame
exercise completed twice with identical exported numeric results and no
generated figures. Its audit status remains `review` because the workflow
correctly retained high correlations, large final shift/esd values, and
forward/reverse path dependence.

Validation details are recorded in
[OPERANDO_VALIDATION.md](OPERANDO_VALIDATION.md).

The later validation-ladder items that require a true electrochemical operando
dataset remain future work and are not claimed as completed.
