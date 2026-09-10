# Formal operational plan v3 — Chat-CBMM

Status: **FROZEN PRE-FORMAL**  
FORMAL RQ data observed before this plan: **NO**  
Runtime M1/M2/M3 modified by this plan: **NO**

This document operationalizes Protocol 2.4. It supersedes the old evaluation
plan that used a static-document RAG baseline, 50 questions and investigator-built
Gold Standards.

## Phase P0 — Pre-FORMAL closure

- [x] DEVELOPMENT globally frozen.
- [x] Runtime M1/M2/M3 unchanged.
- [x] RQ1 matching identity clarified.
- [x] RQ1 irregular-interface representation fixed (absent title, 404 destination, stable parameterized view).
- [x] Route-only RQ1 diagnostic added without replacing primary metrics.
- [x] RQ3 final size fixed at **54 queries**.
- [x] Allocation fixed at **6 queries × 9 strata**.
- [x] Primary RQ3 blind-scoring load fixed at **216 response instances**.
- [x] Statistical analysis pre-specified in `formal_analysis_plan_v1.json`.
- [ ] Expert A independent RQ1 reference frozen.
- [ ] Expert A independent RQ2 reference frozen.
- [ ] Final RQ3 bank/answer key frozen and validated.
- [ ] Formal randomization seed and allocation manifest frozen.
- [ ] Isolated FORMAL persistence verified empty for FORMAL semantic state.
- [ ] Pre-run manifest frozen.
- [ ] Explicit declaration: `FORMAL data collection starts`.

## Phase P1 — RQ1 independent reference

Responsible: Expert A.

Input:
- direct ERP inspection;
- Expert A functional knowledge;
- blank structural reference template.

Restrictions:
- no corresponding FORMAL crawler output;
- no DEVELOPMENT crawler snapshots used as reference.

For `screen` rows, `name` means the visible in-screen title/header after the
screen is opened. A differing menu label may be recorded in `notes`.

Irregular-interface rules fixed before RQ1 freeze:

- a reachable functional screen with no visible page title/header remains in the
  primary census with empty `name` and `title_status: absent` in `notes`;
- a navigation destination that only returns the generic 404 page is recorded in
  `structural_navigation_anomalies.csv` and is not inserted as a functional
  reference `screen`;
- a stable menu-exposed parameterized route is a distinct route-addressable RQ1
  screen view when the parameter changes the functional context, even when the
  same Angular component is reused;
- transient record IDs do not create additional reference screens;
- nested hierarchy uses the complete module path.

These rules do not change the frozen primary dimensions (`module`, `screen`,
`screen_hierarchy`) and do not modify M1/M2/M3 runtime.

Freeze outputs:
- structural reference file SHA256;
- independence declaration;
- reference-harness validation report.

## Phase P2 — RQ2 independent semantic reference

Responsible: Expert A.

Restrictions:
- no corresponding FORMAL SemanticProposal before reference freeze.

Freeze outputs:
- semantic reference canonical SHA256;
- file SHA256;
- exclusions file;
- independence declaration;
- validation report.

## Phase P3 — Final RQ3 bank

Investigators draft from frozen RQ1/RQ2 references plus direct ERP inspection.
Expert A validates/finalizes the answer key.

Exact allocation:
- `locate_screen`: 6
- `screen_purpose`: 6
- `fields_and_search`: 6
- `tables_and_columns`: 6
- `controls_actions_and_navigation`: 6
- `current_route_context`: 6
- `ambiguity_and_clarification`: 6
- `out_of_scope_abstention`: 6
- `mutative_safety`: 6

Total: **54 query_ids**.

Each query is run in:
- A graph ON;
- B graph ON;
- C graph ON;
- C graph OFF.

Primary Expert A blind-scoring load:
**54 × 4 = 216 responses**.

Expert B secondary agreement subset:
**2 query_ids per stratum = 18 queries,
72 blinded responses**.

Before execution freeze:
- bank file and canonical SHA256;
- exact 6-per-stratum validation;
- randomization seed;
- run-plan SHA256;
- secondary-scorer subset IDs.

## Phase P4 — FORMAL persistence

Create/verify isolated FORMAL persistence. Preserve DEVELOPMENT archive.

Expected clean FORMAL semantic state before M2:
- SemanticProposal = 0;
- semantic review actions = 0;
- effective authorized semantic versions = 0;
- semantic Chroma documents = 0.

Do not destructively delete DEVELOPMENT history.

## Phase F1 — Formal RQ1

1. Run frozen formal crawler/acquisition.
2. Freeze raw output/fingerprint.
3. Compute primary RQ1 metrics before any structural HITL:
   - module;
   - screen;
   - screen_hierarchy.
4. Also report diagnostics:
   - screen_route;
   - title match on shared routes;
   - hierarchy match on shared routes.
5. Audit evidence traceability separately.
6. Expert B performs structural HITL only after pre-HITL metrics are frozen.
7. Publish authorized FORMAL structural state.

## Phase F2 — Formal RQ2

1. Generate with frozen qwen3.5:9b / screen-purpose-v14.2 contract.
2. Keep generation failures/timeouts in denominators.
3. Freeze raw SemanticProposal set.
4. Score PRE-HITL against Expert A reference.
5. Expert B performs timed HITL.
6. Freeze decisions and effort.
7. Score POST-HITL.
8. Publish authorized semantic state.

## Phase F3 — Formal RQ3

1. Execute the same 54 frozen query_ids in all four variants.
2. Freeze capture set.
3. Create blinded randomized packet.
4. Expert A scores all 216 responses without condition identity.
5. Expert B scores the pre-frozen stratified subset.
6. Freeze scores before unblinding.
7. Unblind once.
8. Aggregate using `formal_analysis_plan_v1.json`.
9. Per-stratum results are descriptive only.
10. No tuning after observing results.

## Phase F4 — SUS

Condition: C graph ON only.

Target: approximately 15 representative ERP users.
Report actual valid n, mean, median and dispersion. Keep interpretation exploratory.

## Phase F5 — Article

Only after formal artifacts are frozen:
- replace RQ1/RQ2/RQ3/SUS placeholders;
- write integrated discussion;
- write empirical conclusions;
- update abstract;
- update tables/figures if required;
- perform final English translation;
- final journal-format proof.

## Permanent guardrails

- DEVELOPMENT numbers are not formal RQ evidence.
- No static-document RAG primary baseline is reintroduced by inertia.
- No prompt/model/runtime tuning after FORMAL starts.
- Approval rate is not semantic accuracy.
- Carry-forward is not claimed as time savings unless measured.
- Failures/timeouts are not silently removed.
- One Expert A contributes to multiple reference/scoring roles; this correlated
  epistemic-dependency threat must be reported explicitly.


## RQ2 reference-construction clarification (v2)

The frozen RQ1 functional-screen routes define the accounting universe for the
independent RQ2 semantic reference.

Before RQ2 freeze, every frozen RQ1 functional route must be exactly one of:

- included in the semantic reference; or
- explicitly listed in `EXCLUSIONES_RQ2.csv`.

A missing visible page title does not itself make a functional screen
semantically ineligible. RQ2 identity is the normalized route. Use
`title_status="absent"` and `title=null` when no global title/header exists.

ChatGPT may assist only with transcription, normalization, atomic claim
splitting and JSON organization from Expert A statements. It cannot supply
independent functional facts or use DEVELOPMENT/FORMAL SemanticProposal,
chatbot answers or assistant datastores as source material. Expert A must review
and approve every assisted purpose/capability before freeze.

The formal bundle must pass `validate-semantic-bundle` against the frozen RQ1
structural reference before FORMAL M2 generation may begin.
