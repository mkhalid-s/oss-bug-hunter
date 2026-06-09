# Cell #1 Day 3 Pass-1 Candidates

**Total candidates:** 1  (code-quality: 0, edge-case: 1)

## Status rollup

| Status | Count |
|---|---|
| validated | 0 |
| unreproducible | 0 |
| dupe | 0 |
| false-positive | 0 |
| pending | 0 |

## Per-candidate table

| ID | Angle | Status | Summary | Location | OSV/GH dupes |
|---|---|---|---|---|---|
| `ec-1` | edge-case | failed-self-consistency | Object-Id collection path adds JSON null directly to a null-hostile collection,  | `src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java:464-479` | OSV:0 GH:0 |

## Pending work

All candidates have a final_status set.

## Notes

- This is Pass 1. Day 4 re-runs the same prompts in fresh contexts; candidates that don't reappear in 2-of-3 contexts get dropped (self-consistency).
- Status `pending` means the human hasn't filled the validation gates yet — see corresponding YAML under `cell-1/hunt/validation/`.
- OSV/GH dedup counts above are *auto-suggested matches*, not confirmed duplicates. The human reviews each in the scaffold's `gates.dedup` section.
