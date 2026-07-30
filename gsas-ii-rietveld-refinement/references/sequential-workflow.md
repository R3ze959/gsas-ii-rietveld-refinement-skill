# Sequential GSAS-II workflow

This mode covers operando, in-situ, temperature-series, time-series, and other
ordered sets of already integrated one-dimensional powder patterns. It does
not integrate detector images or generate figures.

## Scientific design

1. Refine representative start, middle, end, and transition-region frames
   before running the sequence.
2. Use a calibrated instrument profile and keep it fixed in the production
   sequence. If calibration is unavailable, declare the profile uncalibrated
   and do not compensate by freely varying U/V/W.
3. Keep global phase `Cell` refinement off. Use histogram-dependent HAP
   `Dij/HStrain` terms for frame-dependent lattice changes.
4. Start with background, histogram scale, and justified Dij terms.
5. Continue from the per-frame sequential results with sample displacement,
   constrained phase fractions, and only justified size/microstrain models.
6. Add atomic X/U flags only as an explicit final stage. Occupancy, dopant
   site, and vacancy refinement remain off unless independent evidence and
   constraints justify them.
7. Run both directions: forward from the accepted first-frame anchor and
   reverse from the accepted last-frame anchor.

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
  always retained.
- `--max-anchor-rwp-over-rmin` prevents a formally converged but visibly poor
  endpoint from seeding a sequence.

## Output contract

Each run directory contains:

- `inputs/` and `input_bundle_manifest.json`: verified copies and hashes of
  the source manifest, instrument file, CIFs, and patterns;
- `anchors/` and `anchor_summary.json`;
- `sequences/sequence_base_forward.gpx` and
  `sequences/sequence_base_reverse.gpx`;
- final forward/reverse GPX and per-stage snapshots;
- `results/sequential_results_forward.json/.csv`;
- `results/sequential_results_reverse.json/.csv`;
- `results/sequential_audit.json`;
- `results/sequential_report.md`;
- `results/sequential_report_validation.json`;
- `sequence_run_summary.json`.

No plot is produced.

## Audit semantics

- `fail`: missing frames, nonconvergence, SVD failure, frozen variables, or
  another result-integrity failure.
- `review`: the run completed, but direction dependence, correlation at or
  above 95%, shift/esd above 1, or a robust continuity outlier requires
  scientific review.
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
