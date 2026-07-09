# Archive policy reference

All final refinement deliverables go under the configured archive root:

`GSASII_REFINEMENT_ARCHIVE` or `~/GSAS-II_refinement_results/`

All intermediate generated files go under the configured staging root:

`GSASII_REFINEMENT_STAGING` or `~/GSAS-II_refinement_staging/`

The final Python plot must live inside the corresponding sample archive folder, for example:

`~/GSAS-II_refinement_results/Nb14W3O44_I4m_2405347/NWO514/`

Do not use a separate shared folder such as `Python绘图/` as the final location for sample figures. Do not keep plot CSVs, `TIF`, `PDF`, `SVG`, or plot manifest files in the final archive unless the user explicitly asks for them.

## Final layout

Use this layout:

```text
GSAS-II_refinement_results/
  <cif-key>/
    <sample-id>/
      <sample-id>_result.cif
      <sample-id>_source_model.cif
      <sample-id>_xrd.txt
      <sample-id>_refinement.gpx
      <sample-id>_refinement.lst
      <sample-id>_fit.png
      <sample-id>_python_rietveld.png
      <sample-id>_report.md
      manifest.json
GSAS-II_refinement_staging/
  <sample-id>_<timestamp>/
    intermediate files here
```

`<cif-key>` should classify the result by the refined structure model, for example:

- `Nb14W3O44_I4m_2405347`
- `Nb14W3O44_I4m_high_entropy_parent`
- `custom_cif_<stem>`

## Cleanup rule

After final archive verification, delete process/intermediate files for the current run. Only delete them after:

1. The final archive folder exists.
2. Manifest lists all expected final files.
3. The copied files have nonzero size.
4. The cleanup target is a subdirectory of the configured staging root.

Never delete:

- Original XRD files supplied by the user.
- Original CIF files supplied by the user.
- Existing final archives outside the current sample folder.
- Any directory outside the configured staging root.

Process/intermediate files include:

- Failed or rejected candidate `.gpx` files.
- Scratch CSV/JSON/log files not generated from the selected final `.gpx`.
- Temporary plots from candidate fits.
- Plot CSVs, `TIF`, `PDF`, `SVG`, and plot manifest files generated during figure tuning when the user did not explicitly request keeping them.
- Trial reports, copied CIFs, and helper scripts that are not final deliverables.
- The current run's staging directory after the final archive manifest is verified.

Prefer using `scripts/archive_refinement_results.py --cleanup-staging --staging-dir <run-staging-dir>` so cleanup is guarded by the allowed staging root. If cleanup is deferred because a user has GSAS-II open or wants to inspect candidates, say so explicitly in the final report.

## Final-only meaning

"Only final result" means the final archive keeps selected final deliverables plus manifest. For Python plotting, only the final PNG counts as a final deliverable by default. Temporary candidate `.gpx`, extra plots, logs, JSON scratch files, generated plot CSVs, plot manifests, and failed candidate folders should stay in staging and be removed after verification.
