# Formal operational plan v5 — Chat-CBMM

Status: **FROZEN PRE-FORMAL003 INPUT**

Created: 2026-09-17T01:09:34.666761+00:00

This plan supersedes **Formal Operational Plan v4 only for future FORMAL003 operations**.
It does **not** modify or rewrite Protocol 2.5, Formal Analysis Plan v2, Policy Scope Contract v1,
the frozen RQ1/RQ2 references, RQ3 bank v2, RQ3 execution plan v2, or historical FORMAL artifacts.

## 1. Scientific disposition

- FORMAL001: preserved diagnostic/non-confirmatory iteration; excluded from primary final RQ results.
- FORMAL002: preserved diagnostic/non-confirmatory iteration; excluded from primary final RQ results.
- FORMAL003: **NOT STARTED** at the time this plan is frozen.
- FORMAL003 is described as the final pre-specified evaluation of a stabilized artifact after diagnostic DSRM iterations.

## 2. Frozen base instruments carried forward unchanged

- Protocol 2.5
- Formal Analysis Plan v2
- Policy Scope Contract v1
- RQ1 structural reference + RQ1 reference freeze
- RQ2 semantic reference + exclusion + RQ2 reference freeze
- RQ3 query bank v2
- RQ3 execution plan v2: 216 entries
- RQ3 Expert B subset v2: 18 queries / 72 blind responses
- RQ3 sampling/allocation manifest v2
- Expert A RQ3 bank-validation declaration v2
- M3 A/B/C condition contract
- M2 DEVELOPMENT model-selection freeze
- M3 writer DEVELOPMENT model-selection freeze

All of these inputs remain fingerprinted by the FORMAL003 preparation amendment.

## 3. Frozen RQ3 interpretation

Primary paired comparisons remain:

1. **B − A**: authorized semantic HITL contribution.
2. **C − B**: grounded writer contribution.

Secondary graph ablation remains:

3. **C graph-on − C graph-off**: governed graph-expansion contribution.

Formal bank size remains **54 queries × 4 conditions = 216 responses**.

The nine strata remain descriptive at stratum level.
Primary inference remains the already-frozen paired design in Formal Analysis Plan v2.

## 4. Frozen inter-rater plan

Expert A remains the primary blind RQ3 scorer.

Expert B remains the secondary scorer for the already-frozen stratified subset:

- 18 queries;
- 2 per stratum;
- 72 blind responses.

Report:

- raw agreement;
- Cohen's kappa for `expected_behavior_correct`;
- safety agreement separately on mutually scored mutative-safety items;
- atomic claim-match disagreement descriptively rather than collapsing it into the same kappa.

The prior Expert B HITL exposure caveat remains mandatory.

## 5. Evaluator fatigue controls

These controls do not change query allocation, randomization or rubric.

Expert A:

- 216 blind responses total;
- score in blocks of at most 36 responses;
- recommended minimum 10-minute break between same-day blocks;
- recommended maximum three blocks in one day;
- no intermediate aggregate feedback;
- no condition identity before scoring is frozen.

Expert B:

- 72 blind responses total;
- score in blocks of at most 36 responses;
- no condition identity before scoring is frozen.

## 6. New secondary RQ1 analyses

The only new analysis definitions introduced for FORMAL003 are those in:

`experiments/protocol/formal003_secondary_analysis_contract_v1.json`

They are secondary/descriptive and do not replace primary RQ1 metrics.

They include:

- observability budget;
- deterministic identity-discrepancy taxonomy.

They are frozen before any FORMAL003 output exists.

## 7. Phase P0 — current pre-FORMAL003 state

Completed:

- M1 hardening V1–V5C;
- accepted final M1 DEVELOPMENT FULL;
- M1 source freeze;
- instrument compatibility audit;
- RQ1 route-universe compatibility;
- RQ2 exact-exclusion compatibility;
- RQ3 exact 54×4 compatibility;
- FORMAL003 additive amendment;
- secondary RQ1 analysis pre-specification.

Still required before FORMAL003 START:

- final isolated DEVELOPMENT E2E;
- close only material integration blockers if any;
- GLOBAL experimental freeze;
- final Methods/Results-shell alignment;
- fresh isolated FORMAL003 persistence;
- empty-state checks;
- FORMAL003 pre-run manifest;
- explicit START record.

No output produced during P0/P1 is FORMAL003 RQ evidence.

## 8. Phase P1 — final isolated DEVELOPMENT E2E

Use the frozen M1 artifact as input.

Required path:

M1 frozen canonical
→ isolated DEVELOPMENT PostgreSQL
→ structural governance/publication smoke
→ governed projections
→ M2 qwen3.5:9b
→ semantic validators
→ DEVELOPMENT semantic HITL
→ semantic publication
→ Neo4j/Chroma
→ M3 entity resolution + lexical/dense + RRF + authority re-check
→ governed graph expansion
→ evidence selection
→ deterministic/writer/clarification/abstention smoke.

Forbidden during this E2E:

- use of RQ1 human reference for tuning;
- use of RQ2 formal semantic reference for tuning;
- use of RQ3 formal bank/answer key for tuning.

Use only DEVELOPMENT smoke cases.

If a material integration blocker occurs:

- preserve the failed attempt;
- diagnose it;
- correct only what blocks the formal campaign;
- re-freeze affected runtime lineage as necessary.

Do not optimize merely because an answer could be stylistically or semantically better.

## 9. Phase P2 — GLOBAL experimental freeze

After the E2E passes, freeze at minimum:

- backend source commit/tree;
- frontend source commit/tree if part of the evaluated UI;
- M1/M2/M3 runtime;
- profiles/policies;
- prompts;
- model IDs and immutable/runtime-resolvable model fingerprints;
- embedding model/dimensionality;
- generation parameters;
- retrieval settings;
- graph settings;
- schema/migration revision;
- RQ1 reference/freeze;
- RQ2 reference/exclusion/freeze;
- RQ3 bank/plan/subset/allocation/key-validation artifacts;
- evaluation harness;
- rubrics;
- randomization seed/plan;
- primary statistics;
- secondary-analysis contract;
- privacy-audit sequence;
- evidence-traceability sequence;
- operational plan v5;
- FORMAL003 preparation amendment.

## 10. Phase F0 — fresh FORMAL003 state and START boundary

Before START:

1. Verify GLOBAL freeze.
2. Provision isolated FORMAL003 PostgreSQL.
3. Provision isolated FORMAL003 Neo4j.
4. Provision isolated FORMAL003 vector store.
5. Verify zero DEVELOPMENT semantic rows/projections in FORMAL stores.
6. Verify formal result directory is new/empty.
7. Freeze the pre-run manifest.
8. Freeze explicit privacy and evidence-traceability audit order.
9. Write the immutable FORMAL003 START record.

After step 9, the no-tuning boundary is crossed.

## 11. Phase F1 — RQ1

1. Execute **one** FORMAL003 M1 acquisition.
2. Preserve immutable raw/processed/review artifacts.
3. Build/validate canonical under the frozen contracts.
4. Compute primary RQ1 PRE-HITL metrics.
5. Compute the pre-specified observability budget.
6. Compute the deterministic identity-discrepancy taxonomy.
7. Run privacy audit at the correct pre-canonical boundary.
8. Run canonical validation/privacy contracts appropriate to canonical.
9. Run evidence-traceability audit exactly as pre-specified.
10. Run structural consistency/redundancy/quality audits.
11. Expert B performs structural HITL.
12. Publish authorized structural ACTIVE.
13. Capture projection manifests.

A failed acquisition is preserved and is not automatically rerun.

## 12. Phase F2 — RQ2

1. Generate M2 proposals for the frozen primary-eligible semantic universe.
2. Retain generation failures/timeouts/abstentions in the denominator according to Policy Scope Contract v1.
3. Score RQ2 PRE-HITL.
4. Expert B performs timed semantic HITL.
5. Score RQ2 POST-HITL.
6. Publish authorized semantic knowledge.
7. Capture semantic projection manifests.

Primary eligible universe remains 56 routes under the frozen contract unless a pre-defined eligible generation failure is represented as a failed/zero-prediction outcome; denominators are not decided post hoc.

## 13. Phase F3 — RQ3

Execute the frozen 216-entry plan:

- A graph-on;
- B graph-on;
- C graph-on;
- C graph-off.

Then:

1. Preserve raw outputs and latency.
2. Anonymize/randomize according to frozen plan.
3. Expert A scores all 216 blind responses.
4. Expert B scores the frozen 72-response subset.
5. Freeze all scores before unblinding.
6. Unblind once.
7. Compute frozen paired aggregate analyses.
8. Compute the pre-specified secondary graph ablation.
9. Report nine strata descriptively.
10. Report inter-rater agreement as frozen.

No tuning after observing any FORMAL003 RQ3 output.

## 14. Phase F4 — exploratory SUS

Run only after the evaluated artifact/configuration is fixed.

Treat SUS as exploratory:

- descriptive interpretation;
- no strong population claim;
- report sample and limitations explicitly.

## 15. Phase F5 — article

Only after FORMAL003 artifacts/scores are frozen:

- fill the pre-built Results tables;
- keep DEVELOPMENT characterization explicitly separated;
- report FORMAL001/002 as diagnostic DSRM history;
- answer RQ1/RQ2/RQ3 using FORMAL003 only;
- discuss observability limits without claiming multi-ERP universality;
- report shared-expert dependence and Expert B mitigation;
- report Angular/Fuse single-case transferability limits;
- let negative/null ablation results remain negative/null.

## 16. Permanent guardrails

- No hidden reruns.
- No deleting failed attempts.
- No force-click/JS-click workaround merely to improve counts.
- No use of human references to tune M1.
- No use of the formal RQ3 bank to tune M3.
- No post-START changes to references, bank, key, metrics, denominators, statistics or secondary-analysis definitions.
- No DEVELOPMENT numbers presented as FORMAL003 RQ results.
- No universal-ERP claim from a single-ERP evaluation.
