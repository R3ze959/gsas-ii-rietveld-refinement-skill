# v2.2.0 release notes

This release adds an audited operando and sequential workflow without merging
refinement and visualization into one task.

## Added

- Mandatory request classification before GSAS-II is imported or a GPX is
  created.
- Manifest-driven temperature, time, voltage, capacity, and operando sequences.
- Strict one-dimensional pattern preflight, anchor/checkpoint propagation,
  bounded sequential stages, forward/reverse sensitivity checks, and
  `pass`/`review`/`fail` scientific states.
- A narrow, fail-closed converter for validated STOE WinXPOW
  `RAW_1.06Powdat` frames.
- Exact-key or bounded nearest-time metadata joining without interpolation.
  Nearest-time mode now consumes each metadata row at most once, preserves
  ordered matching, and binds the generated manifest to the synchronization
  audit by SHA-256.
- Conservative sequential candidate comparison and machine-readable audit,
  report, and validation outputs.
- Separate read-only plotting for accepted temperature/operando sequential
  results and for hash-verified experimental operando XRD synchronized with
  electrochemistry.

## Scientific and data-integrity boundaries

- Real refinement requires a local GSAS-II installation plus user-supplied CIF,
  diffraction patterns, and an appropriate instrument parameter file.
- The refinement skill generates no figures. The plotting skill does not run a
  refinement cycle or save a GPX.
- Detector-image integration, automatic unknown-phase discovery, live
  acquisition control, and general-purpose automatic multiphase refinement are
  outside this release.
- A completed run is not automatically accepted: unresolved residual peaks,
  high correlations, missing uncertainties, convergence failures, and
  forward/reverse path dependence retain `review` or `fail` status.
- Experimental heatmaps and stacks without accepted sequential refinement are
  labelled and recorded as experimental displays, not per-frame Rietveld
  results.

## Compatibility note

Experimental-operando plotting now requires a synchronization audit generated
by the current `build_sequential_manifest.py`, including the output manifest
path, manifest SHA-256, and one unique metadata-match record per frame. Rebuild
older synchronization audits before using this route.

## Pre-release validation

- The repository test suite passes 40 unit/integration tests, including
  classifier routing, metadata synchronization, STOE conversion, sequential
  audit logic, and read-only plotting integrity checks.
- A fresh run with real `GSASIIscriptable` completed all 17 public GSAS-II
  Sequential Refinement tutorial frames in both directions, produced no
  figure, and passed machine validation of its report.
- The tutorial run correctly remained scientific `fail`, because the supplied
  model leaves a persistent positive residual near 9.3 degrees above the
  declared hard threshold. This is expected gate behavior, not a software
  completion failure.

## Release hygiene

The release archive contains only the two skill folders, this release note,
the repository README, the public validation record, and the PolyForm
Noncommercial License 1.0.0. It excludes tests, development plans, local
caches, raw/CIF/instrument/GPX data, third-party validation data, and personal
absolute paths.

## License change

- Starting with v2.2.0, original repository code is released under the
  PolyForm Noncommercial License 1.0.0 rather than MIT.
- Noncommercial use is allowed under the exact license terms. Commercial use
  requires a separate written license from the copyright holder.
- This change does not revoke MIT rights already granted for earlier versions.
- GSAS-II, third-party libraries, and third-party datasets retain their own
  licenses and are not relicensed by this repository.
