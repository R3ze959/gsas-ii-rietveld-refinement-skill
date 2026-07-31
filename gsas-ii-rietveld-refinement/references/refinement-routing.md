# Refinement request routing

Run this gate before importing GSAS-II, creating a GPX, selecting a CIF model,
or creating a staging directory. Classification determines data management and
warm-start semantics; it is not a cosmetic label.

## Decision table

| Evidence at entry | Classification | Action |
|---|---|---|
| Exactly one integrated 1D powder pattern | `single_pattern_refinement` | Use the deterministic branched single-pattern driver |
| At least two integrated 1D patterns indexed by a valid CSV manifest with explicit order and a varying experimental coordinate | `sequential_refinement` | Use anchor-gated forward/reverse sequential refinement |
| Multiple integrated 1D patterns explicitly declared to be independent samples | `independent_batch_refinement` | Run isolated single-pattern refinements; never warm-start between samples |
| Multiple integrated 1D patterns without a manifest or batch declaration | `multiple_patterns_ambiguous` | Ask whether they are independent samples or one ordered sequence; do not invoke GSAS-II |
| 2D detector frames or image-stack formats | `detector_integration_required` | Stop and request calibrated integration to 1D patterns |
| A figure-only request | `plotting_handoff` | Stop refinement and route to a plotting skill |
| An existing GPX with no declared operation | `existing_project_ambiguous` | Ask whether the user wants diagnosis, continuation, or plotting |
| Conflicting mode and inputs | `mode_input_conflict` or `conflicting_inputs` | Correct the request before refinement |

Multiphase modeling is not a separate routing category. It is a structural
model choice inside either single-pattern or sequential refinement.

## Deterministic classifier

Single pattern:

```bash
python scripts/classify_refinement_request.py \
  --pattern /path/to/sample.raw
```

Sequential series:

```bash
python scripts/classify_refinement_request.py \
  --manifest /path/to/sequence-manifest.csv
```

Independent batch:

```bash
python scripts/classify_refinement_request.py \
  --declared-mode batch \
  --pattern /path/to/sample-a.xy \
  --pattern /path/to/sample-b.xy
```

Detector data:

```bash
python scripts/classify_refinement_request.py \
  --detector-image /path/to/frame-0001.cbf
```

The classifier returns JSON. `status=ready` permits the named numerical driver,
`status=handoff` leaves the refinement skill, and `status=blocked` or
`status=needs_clarification` forbids a GSAS-II call.

Both numerical drivers repeat the check and embed
`request_classification` in their plan and result provenance. Do not remove or
rewrite that record during candidate selection or archival.

## Plot boundary

- An accepted final GPX plus a conventional observed/calculated/difference
  figure request routes to `rietveld-plotting`.
- Heatmaps, waterfall plots, and cell/phase-fraction versus voltage,
  temperature, time, or capacity plots are visualization products. They do not
  authorize further refinement and must be handled outside this skill.
- If a request contains both refinement and plotting, complete and accept the
  numerical result first. Start plotting as a separate downstream task.

## Detector boundary

Do not treat `.cbf`, `.edf`, `.img`, `.mar*`, detector `.tif/.tiff`, HDF5, or
NeXus frames as powder patterns. Require calibrated detector geometry,
integration settings, masks, polarization treatment, wavelength, and the
resulting 1D patterns with provenance. This skill does not invent those
settings or silently integrate images.
