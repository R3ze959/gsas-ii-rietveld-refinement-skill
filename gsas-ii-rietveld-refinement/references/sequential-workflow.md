# Sequential GSAS-II workflow

Enter this workflow only after the mandatory classifier returns
`sequential_refinement` with `status=ready`.

This mode covers operando, in-situ, temperature-series, time-series, and other
ordered sets of already integrated one-dimensional powder patterns. It does
not integrate detector images or generate figures.

## Scientific design

1. Preflight every one-dimensional pattern before GSAS-II import. Require
   monotonic 2theta, usable point counts, and compatible ranges/steps; preserve
   supplied intensities and weights.
2. Refine representative start, middle, end, requested, and phase-set-boundary
   frames before running the sequence.
3. Use a calibrated instrument profile and keep it fixed in the production
   sequence. If calibration is unavailable, declare the profile uncalibrated
   and do not compensate by freely varying U/V/W.
4. Keep global phase `Cell` refinement off. Use histogram-dependent HAP
   `Dij/HStrain` terms for frame-dependent lattice changes.
5. Start with background, histogram scale, and justified Dij terms.
6. Continue from the per-frame sequential results with sample displacement,
   constrained phase fractions, and only justified size/microstrain models.
7. Add atomic X/U flags only as an explicit final stage after the explicit
   justification, Nobs/Nvars, and correlation gates. Occupancy, dopant
   site, and vacancy refinement remain off unless independent evidence and
   constraints justify them.
8. Accepted internal anchors are checkpoints, not decorative diagnostics.
   Partition each direction into non-overlapping checkpoint segments. An
   endpoint rejection blocks propagation; an internal rejection removes only
   that checkpoint.
9. Run both directions: forward from the accepted first-frame anchor and
   reverse from the accepted last-frame anchor. A failed local segment is
   preserved as partial evidence while independent segments continue.
10. Copy one recorded global reference cell into both sequence bases. Convert
    each checkpoint cell into an equivalent starting HStrain offset so the
    checkpoint lattice state is retained without artificial direction
    dependence from different fixed global cells.
11. Repeat each unchanged sequential stage up to the declared maximum while
    final-cycle shift/esd remains above 1. Stop after the bound and retain a
    review flag; do not chase convergence indefinitely.

The official GSAS-II sequential tutorial motivates the same core separation:
global lattice parameters are shared, while histogram-dependent Dij terms
represent lattice changes in a sequence.

## Portable invocation

Set the local GSAS-II paths:

```bash
export GSASII_DIR="/path/to/GSAS-II"
export GSASII_PYTHON="/path/to/python-that-imports-GSASII"
export GSASII_REFINEMENT_STAGING="/path/to/refinement-staging"
```

Plan first:

```bash
"$GSASII_PYTHON" scripts/run_sequential_refinement.py \
  --sample-id SampleID \
  --manifest /path/to/manifest.csv \
  --cif /path/to/phase-a.cif --phase-name PhaseA \
  --hstrain-mask all \
  --cif /path/to/phase-b.cif --phase-name PhaseB \
  --hstrain-mask 1,1,1,0 \
  --instrument /path/to/instrument.prm \
  --instrument-profile-status calibrated \
  --plan-only
```

If diffraction-frame and electrochemical metadata are separate, first create
an audited manifest. Exact joins are preferred:

```bash
python scripts/build_sequential_manifest.py \
  --frame-index /path/to/frame_index.csv \
  --metadata-csv /path/to/electrochemistry.csv \
  --join-on frame_id \
  --output /path/to/sequence_manifest.csv
```

For nearest-time matching, supply both time-column names and a positive
`--maximum-delta-s`. The builder performs a one-to-one nearest match and never
interpolates metadata. It binds the output manifest hash and every unique
frame-to-metadata match in the synchronization audit.

Then run the exact reviewed plan without `--plan-only`. Optional per-phase
settings are repeated in the same phase order:

```bash
  --size-model isotropic --mustrain-model uniaxial \
  --mustrain-axis 0,1,0 --atom-flags XU \
  --size-model off --mustrain-model isotropic \
  --mustrain-axis 0,0,1 --atom-flags none
```

Useful series controls:

- `--displacement-mode displace-x --goniometer-radius <mm>` declares the
  geometry model.
- `--refine-displacement-in-sequence` releases it only in stage 2.
- `--phase-fractions refine` releases constrained phase fractions only in
  stage 3.
- `--hstrain-mask` accepts `all`, `none`, or a symmetry-sized `1,0,...` mask.
- `--anchor-orders` adds transition-region anchors; start and end anchors are
  always retained. Midpoint and phase-set-boundary anchors are automatic.
- `--max-anchor-rwp-over-rmin` prevents a formally converged but visibly poor
  endpoint from seeding a sequence.
- `--pattern-preflight strict` is the production default.
- `--sequential-stage-max-passes 3` bounds repeated passes of an unchanged
  stage while final-cycle shift/esd remains above 1.
- `--allow-atomic-refinement --atomic-justification "..."` is required before
  any per-frame X/U stage; the driver still applies numerical preflight gates.

## Output contract

Each run directory contains:

- `inputs/` and `input_bundle_manifest.json`: verified copies and hashes of
  the source manifest, instrument file, CIFs, and patterns;
- `anchors/` and `anchor_summary.json`, including optional profile-seed
  acceptance or fallback;
- `sequences/<direction_segment>/sequence_base.gpx`;
- segmented forward/reverse GPX projects and per-stage snapshots;
- `results/sequential_results_forward.json/.csv`;
- `results/sequential_results_reverse.json/.csv`;
- `results/sequential_audit.json`;
- `results/sequential_report.md`;
- `results/sequential_report_validation.json`;
- `sequence_run_summary.json`.

For two or more declared candidate models, run
`select_sequential_candidate.py --run-summary ... --run-summary ...`. It writes
`candidate_summary.json`, rejects `fail`, and ranks direction stability before
median Rwp. Direction deltas that are all within the predeclared audit
tolerance are treated as scientifically equivalent; among those candidates,
the selector compares median Rwp rather than rewarding meaningless extra
decimal-place differences in path sensitivity.

No plot is produced.

## Audit semantics

- `fail`: missing frames, nonconvergence, SVD failure, frozen variables, or
  another result-integrity failure. A persistent positive residual above the
  declared hard fraction of the pattern maximum is also a model failure.
- `review`: the run completed, but direction dependence, correlation at or
  above 95%, final-cycle shift/esd above 1, a missing formal phase-fraction
  ESD, or a robust continuity outlier requires scientific review. GSAS-II's
  `Max shft/sig` is the total parameter displacement over the refinement run;
  record it separately and do not use it as the final-cycle convergence check.
- `pass`: no hard failure and no declared review flag. This still does not
  prove the phase model or a chemical mechanism.

Do not reduce a `review` to `pass` by loosening tolerances after seeing the
answer. Any tolerance change must be declared before candidate selection and
scientifically justified.

## Official validation reference

- Tutorial:
  <https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/SequentialTutorial.htm>
- Exercise archive:
  <https://advancedphotonsource.github.io/GSAS-II-tutorials/SeqRefine/data/SeqTut.zip>

The external tutorial data are validation inputs, not repository content.
Keep them outside version control and respect the source's terms.
