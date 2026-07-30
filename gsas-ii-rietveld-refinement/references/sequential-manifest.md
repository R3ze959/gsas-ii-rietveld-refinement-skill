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

If the phase set changes and phase fractions are to be refined, split the
series into scientifically justified windows or keep phase fractions fixed.
The current production driver rejects one wildcard phase-fraction constraint
across incompatible phase sets.

## Validation rules

- Reject duplicate or blank `frame_id` values.
- Reject duplicate or non-integer `order` values.
- Reject missing, empty, or unreadable pattern files.
- Reject phase names not supplied by `--phase-name`.
- Preserve source order only as provenance; execution order is the sorted
  integer `order`.
- Keep units in column names. Do not silently convert or relabel units.
- Treat acquisition gaps, repeated potentials, and non-monotonic metadata as
  experimental facts. Do not sort by metadata or smooth the trajectory.
