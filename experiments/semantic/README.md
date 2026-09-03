# M2 semantic development

This directory contains **development-only** material for selecting and tuning the
screen-purpose semantic inference configuration before the scientific freeze.

`development_screens_v1.json` is deliberately separate from the final evaluation
bank. Screens used here may be used to improve prompts, schemas, evidence
projection, model choice, or generation parameters, so they must not be counted
as held-out evidence for the final M2 quality estimate.

The current shortlist is task-specific:

- `phi4-mini:3.8b`
- `qwen2.5:14b`
- `qwen3.5:9b`

The benchmark is read-only with respect to semantic persistence. It records
runtime outputs and timing but does **not** decide semantic correctness
automatically. Human review remains the authority for correctness, usefulness,
and whether a proposal should be approved, corrected, or rejected.
