from types import SimpleNamespace

from erp_assistant.retrieval.entity_resolver import (
    CanonicalEntityResolver,
    EntityResolution,
    EntityResolutionCandidate,
    lexical_query_terms,
    normalize_entity_text,
    query_entity_forms,
)
from erp_assistant.retrieval.query_plan import QueryIntent, QueryPlan


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


def test_resolution_exposes_independent_canonical_lexical_and_trigram_rankings():
    ano = item("screen:ano", "screen", "Año", "ano")
    solicitudes = item("screen:solicitudes", "screen", "Solicitudes", "solicitudes")
    resolver = Resolver(
        [
            (ano, 0.8, 0.91),
            (solicitudes, 0.4, 0.70),
        ]
    )

    result = resolver.resolve(
        plan("¿Dónde está el año?"),
        version_id="version-1",
    )

    ano_candidate = next(
        candidate for candidate in result.candidates if candidate.canonical_id == "screen:ano"
    )
    assert ano_candidate.channel_score("normalized_mention") == 1.0
    assert ano_candidate.channel_score("lexical") is not None
    assert ano_candidate.channel_score("trigram") is not None

    assert result.ranking("canonical")[0][0] == "screen:ano"
    assert result.ranking("lexical")[0][0] == "screen:ano"
    assert result.ranking("trigram")[0][0] == "screen:ano"


def test_ambiguous_candidate_ids_are_explicit_for_downstream_fusion_guard():
    first = item("field:ruc-1", "field", "RUC", "ruc")
    second = item("field:ruc-2", "field", "RUC", "ruc")
    resolver = Resolver([(first, 0.0, 0.0), (second, 0.0, 0.0)])

    result = resolver.resolve(
        plan("¿Dónde aparece RUC?", entity_types=("field",)),
        version_id="version-1",
    )

    assert result.status == "ambiguous"
    assert set(result.ambiguous_candidate_ids) == {"field:ruc-1", "field:ruc-2"}


def test_screen_scope_resolves_global_child_ambiguity_without_guessing():
    first = item("field:ruc-current", "field", "RUC", "ruc")
    first.parent_canonical_id = "screen:current"
    second = item("field:ruc-other", "field", "RUC", "ruc")
    second.parent_canonical_id = "screen:other"
    screen_current = item("screen:current", "screen", "Actual", "actual")
    screen_current.parent_canonical_id = "module:one"
    screen_other = item("screen:other", "screen", "Otra", "otra")
    screen_other.parent_canonical_id = "module:two"

    class ScopedResolver(Resolver):
        def _scope_items(self, candidate_ids, *, version_id):
            return {row.canonical_id: row for row in (first, second, screen_current, screen_other)}

    resolver = ScopedResolver([(first, 0.0, 0.0), (second, 0.0, 0.0)])
    result = resolver.resolve(
        plan("¿Cómo busco por RUC aquí?", entity_types=("field",)),
        version_id="version-1",
    )

    assert result.status == "ambiguous"

    scoped = resolver.scope_to_screen(
        result,
        version_id="version-1",
        screen_id="screen:current",
    )

    assert scoped.status == "resolved"
    assert scoped.primary_canonical_id == "field:ruc-current"
    assert [candidate.canonical_id for candidate in scoped.candidates] == ["field:ruc-current"]


def test_screen_scope_follows_table_column_parent_chain():
    column = item("table_column:status", "table_column", "ESTADO", "estado")
    column.parent_canonical_id = "table:current"
    table = item("table:current", "table", "Resultados", "resultados")
    table.parent_canonical_id = "screen:current"
    screen = item("screen:current", "screen", "Actual", "actual")
    screen.parent_canonical_id = "module:one"

    class ScopedResolver(Resolver):
        def _scope_items(self, candidate_ids, *, version_id):
            return {row.canonical_id: row for row in (column, table, screen)}

    resolver = ScopedResolver([])
    from erp_assistant.retrieval.entity_resolver import EntityResolution

    result = EntityResolution(
        query="¿Qué columnas tiene esta pantalla?",
        normalized_query="que columnas tiene esta pantalla",
        candidates=(
            EntityResolutionCandidate(
                canonical_id="table_column:status",
                entity_type="table_column",
                safe_label="ESTADO",
                route=None,
                score=0.95,
                channels=("trigram",),
                matched_terms=("estado",),
            ),
        ),
    )

    scoped = resolver.scope_to_screen(
        result,
        version_id="version-1",
        screen_id="screen:current",
    )

    assert [candidate.canonical_id for candidate in scoped.candidates] == ["table_column:status"]


def test_screen_scope_drops_synthetic_ui_state_title_shadows_for_contextual_navigation():
    screen = item(
        "screen:current",
        "screen",
        "Comprobantes eléctronicos emitidos",
        "comprobantes electronicos emitidos",
    )
    state_a = item(
        "ui_state:a",
        "ui_state",
        "Comprobantes eléctronicos emitidos",
        "comprobantes electronicos emitidos",
    )
    state_a.parent_canonical_id = "screen:current"
    state_b = item(
        "ui_state:b",
        "ui_state",
        "Comprobantes eléctronicos emitidos",
        "comprobantes electronicos emitidos",
    )
    state_b.parent_canonical_id = "screen:current"
    event = item(
        "event:next",
        "event",
        "Siguiente página",
        "siguiente pagina",
    )
    event.parent_canonical_id = "screen:current"

    class ScopedResolver(Resolver):
        def _scope_items(self, candidate_ids, *, version_id):
            return {row.canonical_id: row for row in (screen, state_a, state_b, event)}

    resolver = ScopedResolver([])
    result = EntityResolution(
        query=(
            "¿Cómo avanzo a la siguiente página aquí? "
            'Referencia contextual validada: pantalla "Comprobantes eléctronicos emitidos".'
        ),
        normalized_query=(
            "como avanzo a la siguiente pagina aqui referencia contextual validada "
            "pantalla comprobantes electronicos emitidos"
        ),
        candidates=(
            EntityResolutionCandidate(
                canonical_id="screen:current",
                entity_type="screen",
                safe_label="Comprobantes eléctronicos emitidos",
                route="/admin/cuentasxcobrar/comprobantes",
                score=1.0,
                channels=("normalized_mention",),
                matched_terms=("comprobantes electronicos emitidos",),
            ),
            EntityResolutionCandidate(
                canonical_id="ui_state:a",
                entity_type="ui_state",
                safe_label="Comprobantes eléctronicos emitidos",
                route=None,
                score=1.0,
                channels=("normalized_mention",),
                matched_terms=("comprobantes electronicos emitidos",),
            ),
            EntityResolutionCandidate(
                canonical_id="ui_state:b",
                entity_type="ui_state",
                safe_label="Comprobantes eléctronicos emitidos",
                route=None,
                score=1.0,
                channels=("normalized_mention",),
                matched_terms=("comprobantes electronicos emitidos",),
            ),
            EntityResolutionCandidate(
                canonical_id="event:next",
                entity_type="event",
                safe_label="Siguiente página",
                route=None,
                score=1.0,
                channels=("normalized_mention",),
                matched_terms=("siguiente pagina",),
            ),
        ),
    )

    assert result.status == "ambiguous"
    assert result.ambiguous_labels == ("ui_state:comprobantes electronicos emitidos",)

    scoped = resolver.scope_to_screen(
        result,
        version_id="version-1",
        screen_id="screen:current",
        context_label="Comprobantes eléctronicos emitidos",
    )

    assert scoped.status == "resolved"
    assert [candidate.canonical_id for candidate in scoped.candidates] == [
        "screen:current",
        "event:next",
    ]


def test_screen_scope_keeps_real_same_label_event_ambiguity():
    event_a = item("event:next-a", "event", "Siguiente página", "siguiente pagina")
    event_a.parent_canonical_id = "screen:current"
    event_b = item("event:next-b", "event", "Siguiente página", "siguiente pagina")
    event_b.parent_canonical_id = "screen:current"

    class ScopedResolver(Resolver):
        def _scope_items(self, candidate_ids, *, version_id):
            return {row.canonical_id: row for row in (event_a, event_b)}

    resolver = ScopedResolver([])
    result = EntityResolution(
        query="¿Cómo avanzo a la siguiente página aquí?",
        normalized_query="como avanzo a la siguiente pagina aqui",
        candidates=(
            EntityResolutionCandidate(
                canonical_id="event:next-a",
                entity_type="event",
                safe_label="Siguiente página",
                route=None,
                score=1.0,
                channels=("normalized_mention",),
                matched_terms=("siguiente pagina",),
            ),
            EntityResolutionCandidate(
                canonical_id="event:next-b",
                entity_type="event",
                safe_label="Siguiente página",
                route=None,
                score=1.0,
                channels=("normalized_mention",),
                matched_terms=("siguiente pagina",),
            ),
        ),
    )

    scoped = resolver.scope_to_screen(
        result,
        version_id="version-1",
        screen_id="screen:current",
        context_label="Comprobantes eléctronicos emitidos",
    )

    assert scoped.status == "ambiguous"
    assert scoped.ambiguous_labels == ("event:siguiente pagina",)


def test_resolve_in_screen_filters_candidates_before_global_limit():
    screen = item("screen:ret", "screen", "Retenciones", "retenciones")
    screen.parent_canonical_id = "module:cxp"
    current = item("field:ruc-ret", "field", "RUC", "ruc")
    current.parent_canonical_id = "screen:ret"
    other = item("field:ruc-other", "field", "RUC", "ruc")
    other.parent_canonical_id = "screen:other"

    class ScreenResolver(Resolver):
        def _screen_scope_canonical_ids(self, *, version_id, screen_id):
            assert version_id == "version-1"
            assert screen_id == "screen:ret"
            return {"screen:ret", "field:ruc-ret"}

        def _candidate_rows(self, **kwargs):
            canonical_ids = kwargs.get("canonical_ids")
            rows = self.rows
            if canonical_ids is not None:
                rows = [row for row in rows if row[0].canonical_id in canonical_ids]
            return rows

    resolver = ScreenResolver(
        [
            (other, 0.0, 0.0),
            (screen, 0.0, 0.0),
            (current, 0.0, 0.0),
        ]
    )

    global_result = resolver.resolve(
        plan(
            "¿Cómo busco por RUC en Retenciones?",
            entity_types=("screen", "field"),
        ),
        version_id="version-1",
    )
    scoped = resolver.resolve_in_screen(
        plan(
            "¿Cómo busco por RUC en Retenciones?",
            entity_types=("screen", "field"),
        ),
        version_id="version-1",
        screen_id="screen:ret",
    )

    assert global_result.status == "ambiguous"
    assert scoped.status == "resolved"
    assert {candidate.canonical_id for candidate in scoped.candidates} == {
        "screen:ret",
        "field:ruc-ret",
    }


def test_postgres_statement_can_filter_candidates_to_governed_screen_scope():
    statement = CanonicalEntityResolver._postgres_statement(
        version_id="00000000-0000-0000-0000-000000000001",
        entity_types=("screen", "field", "control"),
        normalized_query="como busco por ruc en retenciones",
        query_forms=query_entity_forms("como busco por ruc en retenciones"),
        alias_targets=set(),
        canonical_ids={"screen:ret", "field:ruc", "control:buscar"},
        limit=20,
    )
    sql = str(statement).upper()

    assert "CANONICAL_ID IN" in sql
    assert "KNOWLEDGE_VERSION_ID" in sql
    assert "CURRENT_REVIEW_STATUS IN" in sql
