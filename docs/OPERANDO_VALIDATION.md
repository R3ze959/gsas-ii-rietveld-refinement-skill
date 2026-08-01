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
- forward and reverse checkpoint segments seeded from accepted start, middle,
  and end anchors;
- one common global reference cell with checkpoint-cell states converted to
  HAP HStrain offsets;
- at most three repeats of each unchanged sequential stage while the maximum
  final-cycle shift/esd remained above 1.

Both directions used the same fixed stage order:

1. background, histogram scale, and `Dij/HStrain`;
2. optional sample geometry;
3. phase fractions and justified sample broadening.

## Results

The robust checkpoint regression completed all 17 frames in both directions.
Every segment and requested stage reported all expected histograms and no
stage-level exception, nonconvergence flag, SVD failure, or missing variable
family.

| Check | Result |
|---|---:|
| Forward completed frames | 17/17 |
| Reverse completed frames | 17/17 |
| Forward Rwp range | 13.8773-17.5923% |
| Forward mean / median Rwp | 15.6346% / 15.9697% |
| Reverse Rwp range | 13.8805-17.3661% |
| Reverse mean / median Rwp | 15.6302% / 15.9637% |
| Direction Rwp delta, median / maximum | 0.00729 / 0.40606 percentage point |
| Forward final-cycle shift/esd, median / maximum | 3.462 / 23.412 |
| Reverse final-cycle shift/esd, median / maximum | 4.518 / 13.942 |
| Maximum exported correlation | 88.26% |
| Phase-fraction sum | 1.000000 within floating-point precision |
| Generated figures | 0 |

Relative to the same checkpoint implementation with only one pass per stage,
the bounded repeated-stage version reduced the maximum forward/reverse Rwp
difference from 1.335 to 0.406 percentage point. It also reduced the maximum
forward Rwp from 19.427% to 17.592% and the maximum reverse Rwp from 18.092% to
17.366%. The median direction difference was already small and changed from
0.00932 to 0.00729 percentage point.

Repeated passes were bounded at three because the maximum shift/esd did not
decrease monotonically for every segment. This limit improves ordinary
stage-wise convergence without allowing an unstable model to iterate
indefinitely.

GPX byte hashes are not used as the reproducibility criterion because GSAS-II
projects may contain internal identifiers and other serialization details.
Scientific comparisons use the machine-readable per-frame outputs.

## Audit interpretation

The final audit status is `fail`, despite full software completion.

No frame was missing and no final stage reported a GSAS-II nonconvergence flag,
SVD failure, frozen variable, missing requested variable family, or
phase-fraction normalization error. The hard failure instead comes from a
persistent positive residual near 9.3 degrees in 11 frames. In both directions
that residual reaches 12.7-19.6% of the observed pattern maximum for most
flagged frames and 13.3% in the last frame. It exceeds the declared 10% hard
model-residual threshold.

The audit also retains review information:

- some final-cycle shift/esd values remain above 1 after the three-pass bound;
- the largest forward/reverse relative cell difference is 0.00318 for the
  minor CuO phase, above the declared tolerance;
- formal mass-fraction ESDs are unavailable for 16 of 17 frames in each
  direction under the present sequential wildcard constraint handling.

The normalized phase fractions remain useful screening values, but they are
not fully uncertainty-qualified quantitative fractions. These limitations are
reported rather than hidden or smoothed. A lower Rwp or completed GSAS-II run
does not overrule a systematic missing peak.

## Acceptance boundary

Validated:

- real `GSASIIscriptable` execution;
- strict pattern/manifest handling and metadata-provenance classification;
- representative anchors, endpoint gates, and real internal checkpoints;
- bounded exact/nearest-time metadata joining without interpolation;
- forward and reverse sequential refinement;
- local segment failure isolation and partial-result preservation;
- common-cell/checkpoint-HStrain conversion;
- multiphase fraction normalization;
- covariance-backed cell and uncertainty export;
- bounded repeated-stage convergence;
- robust persistent-residual gating;
- conservative multi-candidate selection policy;
- transactional input staging with SHA-256 verification;
- machine-verifiable audit/report linkage;
- no-figure boundary.

Still requires system-specific validation before scientific use:

- the user's beamline or laboratory instrument calibration;
- detector-image integration and vendor-format conversion;
- automatic discovery of unknown phases;
- phase appearance/disappearance beyond declared phase-set checkpoints;
- uncertainty-qualified phase fractions for every sequential frame;
- electrochemical interpretation after time alignment;
- chemically justified atomic-coordinate, occupancy, or displacement-parameter
  refinement.
