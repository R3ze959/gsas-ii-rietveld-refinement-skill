# Operando Sequential Refinement Validation

## Scope

This document records external validation of the sequential-refinement mode
against the official GSAS-II Sequential Refinement exercise. It is a software
and workflow validation, not a claim that the tutorial model is the only
physically valid interpretation of the diffraction series.

The validation uses:

- 17 integrated one-dimensional temperature-series patterns;
- the supplied `CuCr2O4.cif` and `CuO.cif` phase models;
- the supplied `OH_00.prm` instrument file;
- a repository-independent CSV manifest that preserves frame order and
  temperature metadata.

Official sources:

- <https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/SequentialTutorial.htm>
- <https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/data/SeqTut.zip>

The downloaded `SeqTut.zip` used for this check had SHA-256:

```text
e46f5d34ea9187647156de25aca9148800d037b77bf34dc762a274f4897345ca
```

The third-party archive and all generated GPX files remain outside version
control.

## Runtime

- Python: 3.13 from a GSAS-II-managed environment
- GSAS-II source revision:
  `fdd954c641c941bc78f60bbd79d6273786fe9909`
- GSAS-II revision description: `v5.6.4/5827`
- Figure generation: disabled

## Refinement configuration

The exercise was run as a two-phase sequence with:

- calibrated instrument profile fixed throughout the production sequence;
- start, middle, and end anchor frames;
- global phase cells refined only in independent anchors and fixed before the
  production sequence;
- histogram-dependent lattice changes represented by HAP `Dij/HStrain`;
- optional sample `DisplaceX` enabled with the declared goniometer radius;
- constrained two-phase fractions;
- phase-specific size and microstrain models;
- no atomic-coordinate or displacement-parameter refinement;
- forward warm start from the first anchor and reverse warm start from the last
  anchor.

Both directions used the same fixed stage order:

1. background, histogram scale, and `Dij/HStrain`;
2. optional sample geometry;
3. phase fractions and justified sample broadening.

## Results

Two independent full runs completed all 17 frames in both directions. Every
stage reported all expected histograms and no stage-level failure.

| Check | Result |
|---|---:|
| Forward completed frames | 17/17 |
| Reverse completed frames | 17/17 |
| Forward Rwp range | 13.8569-17.3931% |
| Forward mean Rwp | 15.6556% |
| Reverse Rwp range | 14.9959-17.4845% |
| Reverse mean Rwp | 16.0799% |
| Forward phase-fraction sum | 1.000000 within floating-point precision |
| Reverse phase-fraction sum | 1.000000 within floating-point precision |
| Input-bundle entries | 21 |
| Source/staged SHA-256 comparisons | 42/42 matched |
| Generated figures | 0 |

For the two independent runs, the maximum absolute difference was exactly zero
for every exported:

- Rwp and GOF value;
- phase mass fraction;
- lattice parameter and angle;
- unit-cell volume.

GPX byte hashes are not used as the reproducibility criterion because GSAS-II
projects may contain internal identifiers and other serialization details.
Reproducibility is evaluated from the scientific numeric outputs.

## Audit interpretation

The final audit status is `review`, not `pass`.

There were no hard failures: no missing frame, failed GSAS-II convergence flag,
SVD failure, frozen variable, missing required result, or phase-fraction
normalization error. However, the audit retained review flags for all 17 frames
because:

- final sequential maximum shift/esd values remained large
  (forward maximum 41.68; reverse maximum 28.34);
- forward and reverse warm starts produced scientifically relevant path
  dependence in some cells and Rwp values;
- some anchor and stage models showed strong parameter correlations, including
  correlations above 95%.

These findings are intentionally not hidden or smoothed. They demonstrate that
the driver can complete and reproduce the official sequence while still
refusing to equate numerical completion or lower Rwp with an unambiguous
physical model.

## Acceptance boundary

Validated:

- real `GSASIIscriptable` execution;
- ordered manifest handling;
- representative anchors and endpoint gates;
- forward and reverse sequential refinement;
- multiphase fraction normalization;
- covariance-backed cell and uncertainty export;
- deterministic numeric reruns;
- transactional input staging with SHA-256 verification;
- machine-verifiable audit/report linkage;
- no-figure boundary.

Still requires system-specific validation before scientific use:

- the user's beamline or laboratory instrument calibration;
- detector-image integration and vendor-format conversion;
- automatic discovery of unknown phases;
- phase appearance/disappearance across changing phase sets;
- electrochemical time alignment for true operando cells;
- chemically justified atomic-coordinate, occupancy, or displacement-parameter
  refinement.
