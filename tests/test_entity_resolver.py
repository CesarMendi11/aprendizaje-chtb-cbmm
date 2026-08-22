from types import SimpleNamespace

from src.hybrid.entity_resolver import (
    CanonicalEntityResolver,
    EntityResolutionCandidate,
    lexical_query_terms,
    normalize_entity_text,
    query_entity_forms,
)
from src.hybrid.query_plan import QueryIntent, QueryPlan


def plan(question, *, entity_types=("screen",)):
    return QueryPlan(
        question=question,
        normalized_question=normalize_entity_text(question),
        intent=QueryIntent.LOCATE_SCREEN,
        target_entity_types=entity_types,
        requires_entity_resolution=True,
        requires_graph_context=True,
        requires_semantic_evidence=False,
        mutative_action=False,
    )


class Resolver(CanonicalEntityResolver):
    def __init__(self, rows, *, aliases=None):
        self.session = None
        self.aliases = aliases or {}
        self.effective = None
        self.rows = rows

    def _candidate_rows(self, **kwargs):
        return self.rows

    def _corrected_items(self, **kwargs):
        return []

    def _effective_payload(self, item):
        return item.source_payload


def item(canonical_id, entity_type, title, normalized_title=None, route=None):
    return SimpleNamespace(
        id=f"db:{canonical_id}",
        canonical_id=canonical_id,
        entity_type=entity_type,
        title=title,
        normalized_title=normalized_title,
        route=route,
        source_payload={"title": title, "label": title, "name": title},
    )


def test_query_forms_include_singularized_span_for_spanish_plural():
    forms = query_entity_forms("donde configuro los anos")
    assert "anos" in forms
    assert "ano" in forms
    assert "configuro los ano" in forms


def test_resolver_resolves_ano_from_natural_plural_mention():
    ano = item(
        "screen:ano",
        "screen",
        "Año",
        "ano",
        "/admin/general/anios",
    )
    resolver = Resolver([(ano, 0.0, 0.0)])

    result = resolver.resolve(
        plan("¿Dónde configuro los años?"),
        version_id="version-1",
    )

    assert result.status == "resolved"
    assert result.primary_canonical_id == "screen:ano"
    assert result.candidates[0].safe_label == "Año"
    assert result.candidates[0].score == 1.0
    assert "normalized_mention" in result.candidates[0].channels


def test_alias_resolves_configured_human_phrase_to_canonical_label():
    ruc = item("field:ruc", "field", "RUC", "ruc")
    resolver = Resolver(
        [(ruc, 0.0, 0.0)],
        aliases={
            "RUC": [
                "identificación tributaria",
                "identificacion tributaria",
            ]
        },
    )

    result = resolver.resolve(
        plan("¿Dónde aparece la identificación tributaria?", entity_types=("field",)),
        version_id="version-1",
    )

    assert result.primary_canonical_id == "field:ruc"
    assert "alias" in result.candidates[0].channels


def test_same_strength_candidates_remain_ambiguous_instead_of_guessing():
    first = item("screen:uno", "screen", "Solicitudes", "solicitudes")
    second = item("screen:dos", "screen", "Solicitudes", "solicitudes")
    resolver = Resolver([(first, 0.0, 0.0), (second, 0.0, 0.0)])

    result = resolver.resolve(
        plan("¿Dónde están las solicitudes?"),
        version_id="version-1",
    )

    assert result.status == "ambiguous"
    assert result.primary_canonical_id is None
    assert result.ambiguous_labels == ("screen:solicitudes",)
    assert result.seed_candidates == ()
    assert [candidate.canonical_id for candidate in result.candidates] == [
        "screen:dos",
        "screen:uno",
    ]




def test_same_label_across_different_entity_types_is_not_false_ambiguity():
    screen = item("screen:ano", "screen", "Año", "ano")
    field = item("field:ano", "field", "Año", "ano")
    resolver = Resolver([(screen, 0.0, 0.0), (field, 0.0, 0.0)])

    result = resolver.resolve(
        plan("¿Dónde está el año?", entity_types=("screen", "field")),
        version_id="version-1",
    )

    assert result.status == "resolved"
    assert result.ambiguous_labels == ()
    assert {candidate.canonical_id for candidate in result.seed_candidates} == {
        "screen:ano",
        "field:ano",
    }


def test_corrected_effective_label_cannot_be_resolved_by_stale_raw_label():
    corrected = item("screen:new", "screen", "Nombre viejo", "nombre viejo")
    corrected.current_review_status = "corrected"

    class CorrectedResolver(Resolver):
        def _effective_payload(self, current):
            return {"title": "Nombre vigente"}

    resolver = CorrectedResolver([(corrected, 1.0, 1.0)])

    stale = resolver.resolve(
        plan("¿Dónde está Nombre viejo?"),
        version_id="version-1",
    )
    current = resolver.resolve(
        plan("¿Dónde está Nombre vigente?"),
        version_id="version-1",
    )

    assert stale.candidates
    assert stale.candidates[0].score < 0.90
    assert "normalized_mention" not in stale.candidates[0].channels
    assert stale.seed_candidates == ()
    assert current.primary_canonical_id == "screen:new"
    assert "normalized_mention" in current.candidates[0].channels


def test_trigram_candidate_handles_typo_without_claiming_exact_match():
    ano = item("screen:ano", "screen", "Año", "ano")
    resolver = Resolver([(ano, 0.0, 0.91)])

    result = resolver.resolve(
        plan("¿Dónde está anp?"),
        version_id="version-1",
    )

    assert result.candidates[0].canonical_id == "screen:ano"
    assert "trigram" in result.candidates[0].channels
    assert "normalized_mention" not in result.candidates[0].channels
    assert "normalized_containment" not in result.candidates[0].channels


def test_lexical_terms_keep_domain_words_and_singular_variants():
    assert lexical_query_terms("donde configuro los anos en general") == (
        "configuro",
        "anos",
        "ano",
        "general",
    )


def test_postgres_statement_contains_authority_filters_full_text_and_trigram():
    statement = CanonicalEntityResolver._postgres_statement(
        version_id="00000000-0000-0000-0000-000000000001",
        entity_types=("screen", "module"),
        normalized_query="donde estan los anos",
        query_forms=query_entity_forms("donde estan los anos"),
        alias_targets=set(),
        limit=20,
    )
    sql = str(statement).upper()

    assert "KNOWLEDGE_VERSION_ID" in sql
    assert "CURRENT_REVIEW_STATUS IN" in sql
    assert "ENTITY_TYPE IN" in sql
    assert "TO_TSVECTOR" in sql
    assert "TO_TSQUERY" in sql
    assert "WORD_SIMILARITY" in sql
    assert "LIMIT" in sql
