# GSAS-II workflow reference

## Local GSAS-II invocation

Use:

```python
import sys
sys.path.insert(0, "/path/to/GSAS-II")
from GSASII import GSASIIscriptable as G2sc
```

Create a project:

```python
gpx = G2sc.G2Project(newgpx="/abs/path/staging/Sample_parent_I4m.gpx")
hist = gpx.add_powder_histogram("/abs/path/sample.txt", "/abs/path/inst_xry.prm")
phase = gpx.add_phase("/abs/path/model.cif", phasename="Sample parent I4/m", histograms=[hist], fmthint="CIF")
gpx.save()
```

Use the user's calibrated Cu Kalpha instrument parameter file when available. If no instrument file is provided, stop and ask for one or state the assumptions explicitly; do not silently substitute an uncalibrated profile.

Audit imported instrument assumptions before final selection. In particular,
`I(L2)/I(L1)=0` means the Kalpha2 contribution is absent; do not let that pass
silently for a Cu Kalpha pattern. Either compare against a standard ratio such
as 0.5, use the user's calibrated instrument file, or state the assumption as a
reason the refinement may differ from a manual GSAS-II run.

## Input checks

XRD:

- Confirm two-column `2theta intensity` data.
- Record angle range, step size, point count, and header lines.
- Check for negative intensity, discontinuities, obvious low-angle artifacts, or range mismatch.

CIF:

- Confirm space group, cell, atom labels, occupancy, and formula.
- Prefer CIF over mol2 for refinement.
- If using a parent or reference CIF, confirm formula, space group, cell parameters, atom labels, and occupancies match the intended material before refinement.

## Staged parameter order

Use this order unless a strong reason says otherwise:

| Stage | Release | Keep fixed |
|---|---|---|
| 0 | none or calculate only | all structure/profile |
| 1 | scale, background | cell, zero, atom coordinates, occupancy |
| 2 | cell | atom coordinates, occupancy |
| 3 | zero | atom coordinates, occupancy |
| 4 | U/V/W | atom coordinates, occupancy |
| 5 | background 8 or 10 | atom coordinates, occupancy |
| 6 | X/Y or microstrain | atom coordinates, occupancy |
| 7 | preferred orientation or justified phase | occupancy and most coordinates |
| 8 | atom xyz/Uiso groups | occupancy |
| 9 | occupancy with constraints only | chemically impossible freedom |

## Candidate testing

Useful limited candidates:

- Background 8, 10, rarely 12.
- X/Y if Lorentzian broadening seems needed.
- Isotropic microstrain for strain-broadened or disorder-broadened samples.
- Size only if broadening is size-like and stable.
- Preferred orientation only with systematic family intensity bias.
- Additional phase only for residual peaks that match a plausible impurity.

Reject:

- Negative X or suspicious negative SH/L as a final recommendation.
- X/Y + microstrain combinations with 100% correlation.
- HStrain with SVD singularities unless there is a strong reason and a stable model.
- Background-only improvements that visibly eat weak peaks.
- Numerically low Rwp results where the full-range fit plot or residual audit still shows major unexplained observed peaks.

## Residual audit gate

Before calling a refinement final:

- Inspect the full 2theta range, not only the publication plot window.
- Find the largest positive observed-minus-calculated residual peaks and list their approximate 2theta positions in the report.
- If those residuals correspond to visible observed peaks without calculated intensity or plausible Bragg ticks, mark the model incomplete instead of claiming the phase explains the main pattern.
- Do not use low Rwp alone to justify a single-phase result when the background level or weighting hides obvious peak mismatches.

## Final Python plotting

After selecting the final `.gpx`, make the final plot with the bundled script rather than redrawing it manually:

```bash
${GSASII_PYTHON:-python} /path/to/gsas-ii-rietveld-refinement/scripts/make_rietveld_plot.py \
  --gpx /abs/path/GSAS-II_refinement_results/<cif-key>/<sample-id>/<sample-id>_refinement.gpx \
  --sample-id SampleID \
  --out-dir /abs/path/GSAS-II_refinement_results/<cif-key>/<sample-id> \
  --x-min 10 --x-max 60 \
  --panel a
```

Defaults:

- Output separate sample figures, not a combined multi-panel figure, unless the user asks for a combined figure.
- Export only `*_python_rietveld.png` into the same sample archive folder as the final XRD and `.gpx`.
- Use the GSAS-II reflection list for HKL labels and Bragg positions; do not borrow labels from reference images.
- Select up to eight intense, separated reflections. HKL labels should stay centered at the real GSAS-II Bragg 2theta positions; avoid sideways label shifts that make a peak label look assigned to the wrong reflection. Use vertical clearance and subtle leader lines when labels need to be lifted above peaks.
- Use `10-60°` as the default lab-XRD plotting window unless the data range or user request calls for a different range.
- Hide y-axis numeric labels by default for relative-intensity XRD figures; use `--show-y-values` only when absolute intensity values matter.
- Do not keep plot CSVs, `TIF`, `PDF`, `SVG`, or plot manifest files in the final archive unless the user explicitly requests them for a specific paper/submission.
- Do not leave the final figure/data only in a shared folder such as `Python绘图/`; that kind of folder is temporary comparison output, not final archive layout.

## Structure-model defaults

- Use the space group and starting model from the user-supplied or clearly cited reference CIF.
- Keep mixed cation or anion occupancy fixed initially unless independent composition or structural evidence supports refinement.
- Keep defect concentrations fixed unless they are constrained and chemically justified.
- Treat doped or compositionally complex samples as a parent-average structure first unless the user provides stronger evidence.
- State that lab Cu Kalpha powder refinement cannot by itself prove exact dopant site occupancy, defect concentration, or full site ordering.
