# RQ2 independent semantic reference contract v2

Status: FROZEN PRE-FORMAL.

This contract is fixed after the independent RQ1 human reference freeze and
before any FORMAL SemanticProposal output.

## Universe

The semantic-reference accounting universe is the set of functional `screen`
routes in the frozen RQ1 structural reference. Generic 404 navigation anomalies
are not part of that functional-screen universe.

Every RQ1 functional screen route must be accounted for exactly once before RQ2
freeze:

- included in `semantic_reference.json`; or
- documented in `EXCLUSIONES_RQ2.csv` with an explicit reason.

Silent omissions are not allowed.

## Screen identity

RQ2 screen identity is the normalized ERP `route`.

`title` is descriptive evidence, not the semantic identity key.

For a screen with a visible global page title/header:

- `title_status="observed"`
- `title="<observed title>"`

For a functional screen with no visible global page title/header:

- `title_status="absent"`
- `title=null`

Absence of a page title is not, by itself, a reason to exclude the screen from
RQ2 if Expert A can determine its function independently.

## Human source and ChatGPT assistance

Functional content must originate from Expert A's direct ERP inspection and
expert knowledge.

ChatGPT may be used only to:

- transcribe Expert A's statements;
- normalize wording;
- split statements into atomic claims;
- organize the JSON structure.

ChatGPT must not introduce unconfirmed functional facts and must not use
SemanticProposal DEVELOPMENT/FORMAL, chatbot answers, PostgreSQL, ChromaDB,
Neo4j or prior LLM semantic outputs as sources for the reference.

Every assisted purpose/capability wording must be reviewed and approved by
Expert A before freeze.

## Freeze

RQ2 can be frozen only when:

- `status="frozen"`;
- no FORMAL SemanticProposal has been seen;
- routes are unique;
- every frozen RQ1 functional screen is included or explicitly excluded;
- every included purpose and capability has Expert A approval;
- the independence declaration is complete;
- `validate-semantic-bundle` passes.
