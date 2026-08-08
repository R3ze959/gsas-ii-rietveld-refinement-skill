# v3.0.0-beta.2 release notes

This prerelease keeps the multiphase-refinement Beta introduced in beta.1 and
updates the read-only `rietveld-plotting` skill. The ordinary
`gsas-ii-rietveld-refinement` skill remains unchanged from v2.2.0.

## Phase-aware multiphase Bragg legends

- Single-phase GPX figures retain the existing green Bragg row and generic
  `Bragg position` legend entry.
- Multiphase GPX figures automatically place each phase on a separate Bragg
  row with a deterministic distinct color.
- The upper-right legend uses exact phase names read from the selected GPX
  histogram rather than a generic Bragg label.
- Coincident reflections remain present in every applicable phase row and are
  never collapsed across phases.
- The fit-statistics block moves downward when required so the expanded phase
  legend does not overlap Rwp, Rp, or GOF.

## Optional phase fractions

- A phase legend receives an `xx.xx wt%` suffix only when GSAS-II
  `ComputeMassFracs()` returns a complete, normalized phase set with finite,
  positive covariance-derived uncertainties for every plotted phase.
- Missing or invalid quantitative information falls back to phase names only.
- The plotting skill never derives phase fractions from peak heights or raw
  HAP Scale values.
- Displayed values are modeled crystalline mass fractions over the represented
  phases; they are not automatically amorphous-corrected or
  sample-role-normalized.
- The optional plot manifest retains the exact values, uncertainties, source,
  phase-to-color mapping, and unchanged-GPX integrity record.

## Validation

- All repository tests pass from the merged `main` source state.
- All three skill directories pass the skill metadata validator.
- Real CuCr2O4/CuO multiphase and PNb9O25 single-phase GPX smoke tests preserve
  the input GPX SHA-256 before and after rendering.
- The release package excludes tests, caches, private paths, raw data, CIFs,
  instrument files, GPX projects, and third-party datasets.

## License

Original repository code remains under the PolyForm Noncommercial License
1.0.0 with the existing required notice:

> Required Notice: Copyright 2026 R3ze959

Noncommercial use is governed by the exact license terms. Commercial use
requires separate written authorization from the copyright holder. Earlier
versions already released under MIT remain available under their original
terms. GSAS-II and all third-party software or datasets retain their own
licenses.
