# GSAS-II workflow reference

## Local GSAS-II invocation

Run bundled scripts with:

```bash
"${GSASII_PYTHON:-python}" \
  "$CODEX_HOME/skills/gsas-ii-rietveld-refinement/scripts/<script>.py"
```

Set `GSASII_DIR` to the local GSAS-II source tree, or pass `--gsasii-path`.
Set `GSASII_PYTHON` to the Python interpreter that can load the installation.
If `CODEX_HOME` is not set, replace the script path with the absolute path to
the installed skill. Always require the user to provide the instrument file;
never embed or silently reuse a private `.prm`.

Preserve fixed-width `.prm` files byte-for-byte. Never regex-rewrite them.

## Input checks

XRD:

- Confirm two-column `2theta intensity` data or provide the correct GSAS-II format hint.
- Record range, step, point count, header lines, negative intensities, discontinuities, and low-angle artifacts.

CIF:

- Confirm formula, space group, cell, atom labels, and occupancies.
- Prefer CIF over mol2.
- Treat the supplied CIF as a hypothesis to verify, not proof that it is the
  correct phase model.

Instrument:

- Declare `--instrument-profile-status calibrated` only when U/V/W came from a suitable standard measured with the relevant setup.
- Otherwise declare `uncalibrated`; keep U/V/W locked by default.
- Audit `I(L2)/I(L1)`. A zero Kalpha2 ratio must not pass silently for a Cu Kalpha pattern.

## Deterministic branched sequence

Preview the exact plan:

```bash
"${GSASII_PYTHON:-python}" \
  "$CODEX_HOME/skills/gsas-ii-rietveld-refinement/scripts/run_staged_refinement.py" \
  --sample-id SAMPLE \
  --xrd /absolute/sample.xye \
  --cif /absolute/model.cif \
  --instrument /absolute/instrument.prm \
  --instrument-profile-status uncalibrated \
  --profile-mode locked \
  --plan-only
```

Run the same command without `--plan-only`. The driver creates:

| Candidate | Parent | Released parameters |
|---|---|---|
| `01_scale_background` | unrefined | scale, background |
| `02_cell_only` | scale/background | cell |
| `03_zero_only` | scale/background | Zero |
| `04_cell_zero_simultaneous` | scale/background | cell and Zero |
| `05_cell_then_zero` | cell-only | Zero while retaining cell |
| `06_zero_then_cell` | Zero-only | cell while retaining Zero |
| `07_profile_w` or `07_profile_uvw` | chosen geometry branch | profile term(s), only after geometry tests |

This branch matrix exposes cell/Zero path dependence and correlation. Do not
replace it with a single `cell -> Zero -> U/V/W` path.

Rules:

- Use `--profile-mode locked` by default.
- Use `w` only as a post-geometry candidate when peak-width mismatch remains.
- Treat `uvw` as exploratory. The driver refuses it for an uncalibrated profile.
- Keep coordinates, occupancies, Uiso, size, strain, and preferred orientation fixed unless a later candidate is independently justified.
- Add microstrain, size, preferred orientation, or another phase as separate descendants after this core sequence; never silently add them to the baseline.

## Candidate summary contract

`candidate_summary.json` is mandatory. It records:

- source paths, sizes, and SHA-256 hashes;
- parent/released parameters for every candidate;
- Rwp, Rp, R-bkg, wR-bkg, RF, RF², and GOF with explicit sources;
- cell and instrument values with covariance-derived esds and `value(esd)` formatting;
- convergence, SVD0, maximum shift/s.u., correlation pairs at or above 80%;
- LST and recalculated Durbin-Watson values;
- the largest positive observed-minus-calculated local maxima;
- warnings and failed/skipped branches.

Do not delete this file when rejected candidate GPX files are cleaned.

## Selection gate

Before selecting a candidate:

1. Compare cell and Zero across all geometry branches.
2. Reject nonconvergence, SVD warnings, nonpositive profile width, or correlation at or above 95%.
3. Inspect the largest positive residual peaks across the full range.
4. Reject background or profile changes that swallow real peaks.
5. Prefer fewer parameters when Rwp changes are marginal.
6. Keep occupancies fixed unless constraints and independent composition evidence exist.

## Canonical report and validation

After the dialectical review, materialize the selected files:

```bash
"${GSASII_PYTHON:-python}" \
  "$CODEX_HOME/skills/gsas-ii-rietveld-refinement/scripts/select_refinement_candidate.py" \
  --candidate-summary /absolute/candidate_summary.json \
  --candidate 07_profile_w
```

This safety-gates the candidate, copies its GPX/LST, exports the result CIF, and
binds their hashes into `candidate_summary.json`. Then generate the report:

```bash
"${GSASII_PYTHON:-python}" \
  "$CODEX_HOME/skills/gsas-ii-rietveld-refinement/scripts/build_validate_refinement_report.py" \
  --candidate-summary /absolute/candidate_summary.json \
  --write-report /absolute/SAMPLE_report.md \
  --validation-output /absolute/report_validation.json
```

The report builder:

- labels profile residuals as `Rwp`, `Rp`, `R-bkg`, and `wR-bkg`;
- labels Bragg residuals separately as `RF` and `RF²`;
- refuses ambiguous `Rb`/`wRb` wording;
- formats crystallographic uncertainties from GPX covariance;
- embeds a machine-verifiable metrics block.

Archive only when `report_validation.json` has `status=pass`.

## Plotting boundary

- Do not create, restyle, copy, or archive a Rietveld figure.
- Preserve the final GPX arrays and reflection lists for the separate `rietveld-plotting` skill.

## Structural-model boundary

- Use the space group, symmetry setting, and composition in the supplied CIF
  only after checking them against the experimental context.
- Keep coordinates, elemental occupancies, and vacancy content fixed initially.
- Use an average parent structure for substituted materials only when it is a
  stated hypothesis, not a hidden default.
- State that laboratory powder XRD alone generally cannot prove dopant identity,
  incorporation, or site occupancy.
