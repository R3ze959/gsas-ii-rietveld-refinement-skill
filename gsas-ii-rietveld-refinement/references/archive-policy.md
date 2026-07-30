# Archive policy reference

All final refinement deliverables go under:

`GSASII_REFINEMENT_ARCHIVE/`

All intermediate generated files go under:

`GSASII_REFINEMENT_STAGING/`

When those environment variables are unset, the scripts use
`~/GSAS-II_refinement_results/` and `~/GSAS-II_refinement_staging/`.

This archive contains refinement deliverables only. Do not add plots or plotting intermediates through this skill.

## Final layout

Use this layout:

```text
GSAS-II_refinement_results/
    <cif-key>/
      <sample-id>/
        <sample-id>_result.cif
        <sample-id>_source_model.cif
        <sample-id>_instrument.prm
        <sample-id>_xrd.txt
        <sample-id>_refinement.gpx
        <sample-id>_refinement.lst
        <sample-id>_report.md
        <sample-id>_candidate_summary.json
        <sample-id>_report_validation.json
        manifest.json
GSAS-II_refinement_staging/
    <sample-id>_<timestamp>/
      intermediate files here
```

`<cif-key>` should classify the result by the refined structure model, for example:

- `compound_space-group_reference`
- `substituted_parent_average`
- `custom_cif_<stem>`

## Cleanup rule

Always use `scripts/archive_refinement_results.py`. It performs this transaction:

1. Validate every required input, including the exact instrument file, `candidate_summary.json`, and a `report_validation.json` with `status=pass`.
2. Reject any archive/staging path containment or unsafe cleanup target.
3. Copy into a sibling temporary directory without touching the existing archive.
4. Compare every source/destination SHA-256 and verify all required roles.
5. Write and verify the manifest.
6. Atomically rename the transaction into place. With `--replace`, first rename the old archive to a rollback backup; restore it if installation fails.
7. Reverify the installed archive.
8. Only then remove the exact run staging directory and record cleanup status.

This order is mandatory. Never delete an existing archive before new inputs and
the complete transaction have passed validation.

Never delete:

- Original XRD files supplied by the user.
- Original CIF files supplied by the user.
- Existing final archives outside the current sample folder.
- Any directory outside the configured `GSASII_REFINEMENT_STAGING` root.

Process/intermediate files include:

- Failed or rejected candidate `.gpx` files.
- Scratch CSV/JSON/log files other than the retained candidate summary and report validation.
- Trial reports, copied CIFs, and helper scripts that are not final deliverables.
- The current run's staging directory after the final archive manifest is verified.

Prefer using `scripts/archive_refinement_results.py --cleanup-staging --staging-dir <run-staging-dir>` so cleanup is guarded by the allowed staging root. If cleanup is deferred because a user has GSAS-II open or wants to inspect candidates, say so explicitly in the final report.

## Final-only meaning

"Only final result" means the final archive keeps the selected XRD, source/result CIF, instrument file, `.gpx`, `.lst`, canonical report, candidate summary, report validation, and manifest. Temporary candidate `.gpx`, logs, scratch files, and failed candidate folders should stay in staging and be removed after verification.
