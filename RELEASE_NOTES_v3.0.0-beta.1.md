# v3.0.0-beta.1 release notes

This prerelease publishes two skill updates only:

1. the new `gsas-ii-multiphase-refinement` **Beta**;
2. the upgraded `rietveld-plotting` skill.

The ordinary `gsas-ii-rietveld-refinement` directory is unchanged from
v2.2.0.

## Multiphase refinement beta

- Routes one-pattern QPA, fixed-phase-set sequential work, segmented
  changing-phase-set work, and unknown-phase cases before creating a GPX.
- Requires declared structural models and audits CIF composition, mass, cell,
  and GSAS-II import consistency before quantification.
- Uses real GSAS-II mass-fraction computation and covariance-backed
  uncertainties rather than normalized raw scale factors.
- Applies fail-closed gates for convergence, SVD truncation, major unexplained
  residuals, phase-set integrity, model repeatability, and invalid evidence
  contracts.
- Records preferred-orientation and microabsorption sensitivity, trace-phase
  screening, controlled cell sensitivity, internal-standard amorphous
  calculations, and constrained dopant-model comparisons without silently
  converting them into accepted claims.
- Resolves `Shift`, `DisplaceX`, or `DisplaceY` from the imported instrument
  geometry instead of assuming a single sample-position parameter.
- Preserves candidate summaries, protocol hashes, phase-model audits, selected
  GPX projects, prediction archives, and post-unblinding scores.

### Beta boundary

The skill is intended for declared-phase research workflows and reproducible
evaluation. It does not perform unknown-phase identification, prove dopant
content or crystallographic site occupancy from an unconstrained fit, or make
an automatic publication-readiness decision. Real-pattern refinements remain
`review` whenever correlations, residuals, preferred orientation,
microabsorption, model sensitivity, or uncertainty evidence require expert
judgment.

The deterministic two-phase simulation is a software/QPA regression test, not
real-sample validation. Previously opened reference datasets are retained as
development regression evidence, not presented as a new independent blind
test. These limits are why the skill is released as Beta.

## Multiphase plotting update

- Single-pattern GPX figures can place Bragg ticks on one labelled row per
  phase while preserving the locked single-phase layout.
- Temperature and operando series can plot one or all declared phases without
  interpolating cell parameters across absent-phase frames.
- Covariance-backed phase fractions can be rendered with formal error bars,
  phase-absence gaps, and phase-set boundary markers.
- Source patterns, result JSON files, audit files, and GPX projects remain
  hash-checked before and after plotting; the plotting skill never refines or
  saves a GPX.

## Validation and release hygiene

- Both skill directories pass the skill metadata validator.
- A clean staged-tree run passes all 87 repository-owned tests. They cover QPA
  core logic, phase-model import audits,
  common-problem contracts, sample-position geometry, multiphase Bragg rows,
  multiphase cell trajectories, and phase-fraction plots.
- The release archive excludes tests, caches, private paths, raw data, CIFs,
  instrument files, GPX projects, third-party datasets, and development-only
  material.
- The archive contains only the two updated skill directories, this release
  note, the README, and the repository license.

## License

Original repository code remains under the PolyForm Noncommercial License
1.0.0 with the existing required notice:

> Required Notice: Copyright 2026 R3ze959

Noncommercial use is governed by the exact license terms. Commercial use
requires separate written authorization from the copyright holder. Earlier
versions already released under MIT remain available under their original
terms. GSAS-II and all third-party software or datasets retain their own
licenses.
