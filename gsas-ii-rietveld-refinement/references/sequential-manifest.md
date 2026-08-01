# Sequential manifest

Use UTF-8 CSV. One row represents one already integrated one-dimensional
powder pattern. The driver hashes every pattern and copies all inputs into the
run bundle before refinement.

## Required columns

| Column | Meaning |
|---|---|
| `frame_id` | Stable, unique identifier for the experimental frame |
| `order` | Unique integer execution and reporting order |
| `pattern_path` | Absolute path or path relative to the manifest |

At least one supported numeric metadata column must vary across two or more
frames unless `--allow-missing-metadata` is deliberately used for a
file-order-only test.

## Standard numeric metadata

- `time_s`
- `temperature_K`
- `voltage_V`
- `current_mA`
- `capacity_mAh`
- `state_of_charge`

Additional columns are preserved as text metadata. Never infer missing
temperature, voltage, current, capacity, state of charge, or timestamps from
filenames.

## Joining separate experimental metadata

When frame paths and electrochemical/thermal metadata are stored separately,
use `scripts/build_sequential_manifest.py`.

- Prefer an exact one-to-one join on `frame_id` or `order`.
- Nearest-time joining requires explicit frame/metadata time-column names and
  a positive maximum time delta.
- A metadata row can be consumed only once.
- Any unmatched diffraction frame blocks manifest creation.
- No interpolation, smoothing, forward filling, or filename-derived metadata
  is allowed.

The builder writes the manifest atomically and creates a sibling
`<manifest>.sync.json` that records the join mode, match counts, unused
metadata rows, time deltas, and `interpolation_performed=false`. Nearest-time
matching is ordered and one-to-one: one metadata row cannot seed two XRD
frames. The audit also binds the generated manifest path and SHA-256 and keeps
one explicit match record per frame, so a stale or unrelated audit cannot be
silently reused.

## Phase declarations

`phase_set` is optional. When blank, every supplied phase is active. When
present, separate phase names with `;`, `|`, or `,`; names must exactly match
the corresponding `--phase-name` values.

Example:

```csv
frame_id,order,pattern_path,time_s,voltage_V,phase_set
f0000,0,patterns/f0000.xy,0,3.000,Host
f0001,1,patterns/f0001.xy,300,2.950,Host
f0002,2,patterns/f0002.xy,600,2.900,Host;Product
```

Phase-set changes automatically create anchor/checkpoint boundaries. If phase
fractions are refined, no checkpoint segment may cross a phase-set change.
Use scientifically justified windows or keep fractions fixed; never apply one
wildcard fraction constraint across incompatible phase sets.

## Validation rules

- Reject duplicate or blank `frame_id` values.
- Reject duplicate or non-integer `order` values.
- Reject missing, empty, or unreadable pattern files.
- Under production `--pattern-preflight strict`, reject non-monotonic 2theta,
  fewer than 20 numeric points, or materially incompatible frame ranges/steps.
- Reject phase names not supplied by `--phase-name`.
- Preserve source order only as provenance; execution order is the sorted
  integer `order`.
- Keep units in column names. Do not silently convert or relabel units.
- Treat acquisition gaps, repeated potentials, and non-monotonic metadata as
  experimental facts. Do not sort by metadata or smooth the trajectory.
- Classify metadata provenance as time-synchronized, ordered experimental
  coordinates, or file-order-only. File-order-only runs are exploratory even
  when deliberately enabled.
