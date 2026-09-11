# Formal operational plan v4 — Chat-CBMM

Status: **FROZEN PRE-FORMAL002**
FORMAL RQ data observed before this plan: **YES — FORMAL001 RQ1 diagnostic only; non-confirmatory**
Confirmatory FORMAL data observed before this plan: **NO**
Runtime M1/M2/M3 modified by this plan: **NO**

This document operationalizes Protocol 2.5 and Formal Analysis Plan v2.
It supersedes Formal Operational Plan v3 for the fresh FORMAL002 campaign.
Historical v1-v3 artifacts and FORMAL001 artifacts remain preserved.

## Historical disposition — FORMAL001

FORMAL001 crossed the formal boundary and completed structural acquisition plus
pre-structural-HITL RQ1 evaluation. It did not execute structural HITL, RQ2,
RQ3 or SUS.

FORMAL001 is permanently classified as **diagnostic/non-confirmatory** because
it exposed a pre-existing mismatch between:

- the frozen acquisition policy, which deliberately blocks
  `/admin/seguridad`; and
- the frozen evaluation references, which contain eight functional Seguridad
  screens.

FORMAL001 must not be reported as the final confirmatory result, deleted,
overwritten or rerun under the same attempt identifier.

## Phase P0 — Pre-FORMAL002 closure

Completed:

- [x] FORMAL001 disposition frozen.
- [x] POLICY_BLOCKED scope contract frozen.
- [x] RQ1 scoring harness reconciled with POLICY_BLOCKED.
- [x] RQ2 scoring harness reconciled with POLICY_BLOCKED.
- [x] Independent RQ1 structural reference preserved unchanged.
- [x] Independent RQ2 semantic reference preserved unchanged.
- [x] RQ3 bank v1 preserved unchanged.
- [x] RQ3 bank v2 frozen after Expert A review.
- [x] RQ3 bank v2 contains 54 queries, 6 × 9 strata.
- [x] FORMAL002 RQ3 execution plan frozen at 216 variants.
- [x] Expert B secondary subset frozen at 18 queries / 72 responses.
- [x] Formal Analysis Plan v2 frozen.
- [x] Protocol 2.5 frozen.
- [x] Formal Operational Plan v4 frozen.

Still required before FORMAL002 starts:

- [ ] Global DEVELOPMENT Freeze v2.
- [ ] Fresh isolated FORMAL002 persistence verified empty.
- [ ] Formal pre-run manifest for FORMAL002 frozen.
- [ ] Explicit FORMAL002 start record.

No RQ output generated during these preparation steps is FORMAL002 evidence.

## RQ1 frozen reference and POLICY_BLOCKED scope

The independent human reference remains unchanged:

- functional reference screens: 65;
- policy-blocked functional screens: 8;
- policy-eligible primary screen universe: 57.

The blocked prefix is `/admin/seguridad`.

Primary RQ1 metrics remain:

- module;
- screen;
- screen_hierarchy;
- TP / FP / FN;
- precision / recall / F1.

For primary metrics, POLICY_BLOCKED items do **not** enter TP/FP/FN. They are
reported separately because the crawler was explicitly unauthorized to acquire
them.

The Seguridad module is likewise reported as policy-blocked when all of its
functional screens are blocked.

Transparency diagnostic:

- retain all-reference route coverage over all 65 functional reference screens;
- label it explicitly non-primary;
- do not reinterpret its eight expected policy-blocked misses as technical
  crawler failures.

Any unexpected acquisition under the blocked prefix is a policy violation and
must be reported.

Generic 404 navigation anomalies continue to follow the previously frozen
irregular-interface contract and are not automatically functional-screen FNs.

## RQ2 frozen reference and eligibility

The independent semantic reference remains unchanged:

- 64 screens included in the semantic reference;
- 1 explicit semantic exclusion;
- 8 included semantic-reference screens are POLICY_BLOCKED;
- 56 screens are primary RQ2 eligible.

A route is primary RQ2 eligible only when:

1. it is included in the frozen semantic reference; and
2. it is authorized by the frozen acquisition policy.

POLICY_BLOCKED semantic-reference screens are reported separately and do not
enter PRE/POST-HITL semantic quality denominators because governed evidence is
not authorized for those routes.

For an otherwise eligible route, generation failure, timeout, abstention or
unavailable output remains in the denominator as a not-available /
zero-prediction output.

`/admin/home` (Dashboard) is explicitly eligible. Its absent global title does
not remove it from RQ2. If the frozen generator cannot produce an output for
Dashboard, that failure remains measurable.

## RQ3 frozen bank v2

Formal RQ3 uses `rq3-formal-query-bank-v2`.

Fixed design:

- 54 query IDs;
- 9 strata;
- 6 queries per stratum;
- A graph ON;
- B graph ON;
- C graph ON;
- C graph OFF;
- 216 primary blinded responses;
- 18-query Expert B secondary subset;
- 72 Expert B blinded responses.

Compared with bank v1, only Q003, Q022, Q030 and Q034 were replaced because
their original expected-answer content depended on POLICY_BLOCKED Seguridad
knowledge. Fifty queries are unchanged.

The replacements were validated by Expert A from already-frozen human
references. FORMAL001 never executed RQ3.

FORMAL002 randomization seed: **14011105**.

No query, answer key, allocation or condition definition may be changed after
FORMAL002 begins.

## Phase F1 — FORMAL002 RQ1

1. Start from fresh isolated FORMAL002 persistence.
2. Run the frozen crawler/acquisition exactly once.
3. Preserve raw output and fingerprints even if the run fails.
4. Classify reference scope using the frozen POLICY_BLOCKED contract.
5. Compute primary RQ1 metrics over policy-eligible items before structural HITL.
6. Report POLICY_BLOCKED items separately.
7. Report all-reference route coverage as a non-primary transparency diagnostic.
8. Perform the deterministic evidence-traceability audit.
9. Expert B performs structural HITL only after pre-HITL metrics are frozen.
10. Publish corrected/authorized FORMAL002 structural ACTIVE.
11. Freeze structural Chroma and Neo4j projection manifests.

Do not automatically rerun a failed FORMAL002 acquisition.

## Phase F2 — FORMAL002 RQ2

1. Use only the 56 policy-eligible semantic screens for primary PRE/POST scoring.
2. Generate with frozen qwen3.5:9b / screen-purpose-v14.2.
3. Retain eligible generation failures/timeouts/abstentions in denominators.
4. Freeze the raw proposal set before human review.
5. Score PRE-HITL against the unchanged frozen Expert A semantic reference.
6. Expert B performs timed semantic HITL.
7. Freeze decisions and effort.
8. Score POST-HITL against the same reference.
9. Publish authorized semantic Chroma state.

Dashboard remains eligible even if generation produces no usable claim.

## Phase F3 — FORMAL002 RQ3

1. Execute the frozen 54-query v2 bank in all four variants.
2. Use the already-frozen execution plan and seed 14011105.
3. Freeze the capture set.
4. Create the blinded randomized packet.
5. Expert A scores all 216 responses without condition identity.
6. Expert B scores the frozen 18-query secondary subset.
7. Freeze scores before condition identities are unblinded.
8. Unblind once.
9. Aggregate using Formal Analysis Plan v2.
10. Per-stratum results remain descriptive only.
11. No tuning after observing FORMAL002 outputs.

Primary comparisons remain:

- B − A: contribution of authorized semantic HITL knowledge;
- C − B: contribution of the grounded writer.

Secondary comparison:

- C graph ON − C graph OFF: contribution of governed graph expansion.

## Phase F4 — SUS

Use condition C graph ON only.

Target approximately 15 representative ERP users.

Report actual valid n, mean, median and dispersion. Interpretation remains
exploratory and is not the main causal evidence for RQ3.

## Phase F5 — Article

Only after FORMAL002 artifacts and scoring are frozen:

- populate RQ1/RQ2/RQ3/SUS results;
- describe FORMAL001 transparently as an aborted diagnostic attempt;
- distinguish POLICY_BLOCKED coverage from technical acquisition error;
- report Dashboard/eligible generation failures rather than removing them;
- report threats to validity, including Expert A role overlap;
- write integrated discussion and conclusions;
- finalize tables, figures, abstract and journal formatting.

## Permanent guardrails

- FORMAL001 is preserved but is not confirmatory evidence.
- DEVELOPMENT results are not formal RQ evidence.
- No static-document RAG primary baseline is reintroduced.
- No prompt/model/runtime tuning after the new global DEVELOPMENT freeze.
- No FORMAL002-driven change to metrics, query bank, answer keys or eligibility.
- POLICY_BLOCKED is not a technical false negative.
- Eligible RQ2 failures/timeouts/abstentions remain in denominators.
- Approval rate is not semantic accuracy.
- Carry-forward is not claimed as measured time saving.
- Failures are never silently deleted.
- Expert A's overlapping reference/validation/scoring roles remain an explicit
  validity threat.
