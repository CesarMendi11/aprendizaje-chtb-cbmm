from src.hybrid.conversation_context import (
    ConversationContextMode,
    ConversationContextResolver,
    ConversationEntity,
    ConversationState,
    render_missing_context_clarification,
)
from src.hybrid.entity_resolver import EntityResolution, EntityResolutionCandidate
from src.hybrid.query_plan import QueryIntent, QueryPlanner


SCREEN = ConversationEntity(
    canonical_id="screen:ano",
    entity_type="screen",
    safe_label="Año",
    route="/admin/general/anios",
)
MODULE = ConversationEntity(
    canonical_id="module:general",
    entity_type="module",
    safe_label="General",
)


def state(*, version="v1"):
    return ConversationState(
        erp_id="erp:test",
        knowledge_version=version,
        current_screen=SCREEN,
        current_module=MODULE,
        resolved_entities=(SCREEN, MODULE),
        last_intent="LOCATE_SCREEN",
        last_answer_decision="DETERMINISTIC_ANSWER",
        relevant_evidence_refs=("screen:ano",),
        turn_index=1,
    )


def resolution(*candidates):
    return EntityResolution(
        query="q",
        normalized_query="q",
        candidates=tuple(candidates),
    )


def candidate(
    canonical_id,
    label,
    *,
    entity_type="screen",
    score=1.0,
    channels=("normalized_mention",),
):
    return EntityResolutionCandidate(
        canonical_id=canonical_id,
        entity_type=entity_type,
        safe_label=label,
        route=None,
        score=score,
        channels=channels,
        matched_terms=(label.casefold(),),
    )


def resolve(question, direct, previous=None):
    planner = QueryPlanner()
    return ConversationContextResolver(query_planner=planner).resolve(
        question,
        previous,
        query_plan=planner.plan(question),
        direct_resolution=direct,
        erp_id="erp:test",
        knowledge_version="v1",
    )


def test_follow_up_purpose_inherits_governed_screen():
    result = resolve("¿Y para qué sirve?", resolution(), state())

    assert result.mode == ConversationContextMode.CONTEXTUALIZED
    assert result.reason == "governed_entity_reference"
    assert result.inherited_entities == (SCREEN,)
    assert 'pantalla "Año"' in result.effective_question
    assert QueryPlanner().plan(result.effective_question).intent == QueryIntent.SCREEN_PURPOSE


def test_elliptical_columns_question_inherits_screen_without_pronoun():
    result = resolve("¿Qué columnas tiene?", resolution(), state())

    assert result.mode == ConversationContextMode.CONTEXTUALIZED
    assert QueryPlanner().plan(result.effective_question).intent == QueryIntent.LIST_COLUMNS
    assert 'pantalla "Año"' in result.effective_question


def test_explicit_new_entity_wins_over_previous_screen():
    direct = resolution(candidate("screen:usuarios", "Usuarios"))
    result = resolve("¿Y dónde está Usuarios?", direct, state())

    assert result.mode == ConversationContextMode.DIRECT
    assert result.reason == "current_turn_entity"
    assert result.effective_question == "¿Y dónde está Usuarios?"
    assert result.inherited_entities == ()


def test_strong_trigram_typo_is_not_overwritten_by_previous_context():
    direct = resolution(
        candidate(
            "screen:usuarios",
            "Usuarios",
            score=0.83,
            channels=("trigram",),
        )
    )
    result = resolve("¿Y dónde está Usuariso?", direct, state())

    assert result.mode == ConversationContextMode.DIRECT
    assert result.effective_question == "¿Y dónde está Usuariso?"


def test_context_reference_without_antecedent_requires_clarification():
    result = resolve("¿Para qué sirve esa pantalla?", resolution(), None)

    assert result.mode == ConversationContextMode.CLARIFICATION_REQUIRED
    assert result.reason == "conversation_reference_missing"
    assert result.effective_question == "¿Para qué sirve esa pantalla?"


def test_state_from_other_knowledge_version_is_not_reused():
    result = resolve("¿Y para qué sirve?", resolution(), state(version="old"))

    assert result.mode == ConversationContextMode.CLARIFICATION_REQUIRED
    assert result.reason == "conversation_state_stale_or_foreign"


def test_no_contextual_cue_stays_direct_when_unresolved():
    result = resolve(
        "Explícame la estructura de permisos corporativos complejos",
        resolution(),
        state(),
    )

    assert result.mode == ConversationContextMode.DIRECT
    assert result.reason == "no_context_reference"


def test_what_can_i_do_here_is_screen_purpose_intent():
    plan = QueryPlanner().plan("¿Y qué puedo hacer ahí?")

    assert plan.intent == QueryIntent.SCREEN_PURPOSE
    result = resolve("¿Y qué puedo hacer ahí?", resolution(), state())
    assert result.mode == ConversationContextMode.CONTEXTUALIZED


def test_next_state_keeps_only_governed_entities_and_scope():
    resolver = ConversationContextResolver()
    plan = QueryPlanner().plan("¿Dónde configuro los años?")
    next_state = resolver.next_state(
        None,
        erp_id="erp:test",
        knowledge_version="v1",
        query_plan=plan,
        answer_decision="DETERMINISTIC_ANSWER",
        sources=[
            {
                "canonical_id": "screen:ano",
                "entity_type": "screen",
                "safe_label": "Año",
                "screen_route": "/admin/general/anios",
            },
            {
                "canonical_id": "module:general",
                "entity_type": "module",
                "safe_label": "General",
            },
        ],
        clarification_candidates=[],
        evidence_ids=["screen:ano", "module:general", "screen:ano"],
    )

    assert next_state.current_screen == SCREEN
    assert next_state.current_module == MODULE
    assert next_state.turn_index == 1
    assert next_state.relevant_evidence_refs == ("screen:ano", "module:general")
    assert [row.canonical_id for row in next_state.resolved_entities] == [
        "screen:ano",
        "module:general",
    ]


def test_next_state_preserves_screen_on_contextual_answer_without_module_source():
    resolver = ConversationContextResolver()
    plan = QueryPlanner().plan("¿Para qué sirve Año?")
    next_state = resolver.next_state(
        state(),
        erp_id="erp:test",
        knowledge_version="v1",
        query_plan=plan,
        answer_decision="DETERMINISTIC_ANSWER",
        sources=[SCREEN.as_dict()],
        clarification_candidates=[],
        evidence_ids=["screen:ano", "semantic:purpose"],
    )

    assert next_state.current_screen == SCREEN
    assert next_state.current_module == MODULE
    assert next_state.turn_index == 2



def test_next_state_clears_previous_module_when_switching_to_erp_root_screen():
    resolver = ConversationContextResolver()
    plan = QueryPlanner().plan("¿Y dónde está Dashboard?")
    dashboard = {
        "canonical_id": "screen:dashboard",
        "entity_type": "screen",
        "safe_label": "Dashboard",
        "screen_route": "/admin/home",
    }
    erp = {
        "canonical_id": "erp:test",
        "entity_type": "erp_system",
        "safe_label": "ERP Test",
    }

    next_state = resolver.next_state(
        state(),
        erp_id="erp:test",
        knowledge_version="v1",
        query_plan=plan,
        answer_decision="DETERMINISTIC_ANSWER",
        sources=[dashboard, erp],
        clarification_candidates=[],
        evidence_ids=["screen:dashboard", "erp:test"],
    )

    assert next_state.current_screen is not None
    assert next_state.current_screen.canonical_id == "screen:dashboard"
    assert next_state.current_module is None
    assert next_state.turn_index == 2


def test_missing_context_clarification_is_safe_and_specific():
    answer = render_missing_context_clarification("conversation_reference_missing")
    assert "pantalla o un módulo" in answer
    assert "screen:" not in answer
    assert "module:" not in answer


def test_contextual_child_ambiguity_reuses_governed_screen_scope():
    direct = resolution(
        candidate("field:ruc-a", "RUC", entity_type="field"),
        candidate("field:ruc-b", "RUC", entity_type="field"),
    )

    result = resolve("¿Cómo busco por RUC aquí?", direct, state())

    assert result.mode == ConversationContextMode.CONTEXTUALIZED
    assert result.reason == "governed_entity_reference"
    assert result.inherited_entities == (SCREEN,)
    assert 'pantalla "Año"' in result.effective_question


def test_contextual_same_screen_mention_does_not_disable_scope_reuse():
    direct = resolution(candidate("screen:ano", "Año"))

    result = resolve("¿Cómo creo un nuevo año aquí?", direct, state())

    assert result.mode == ConversationContextMode.CONTEXTUALIZED
    assert result.inherited_entities == (SCREEN,)


def test_contextual_reference_still_allows_explicit_different_screen_switch():
    direct = resolution(candidate("screen:dashboard", "Dashboard"))

    result = resolve("¿Y dónde está Dashboard?", direct, state())

    assert result.mode == ConversationContextMode.DIRECT
    assert result.reason == "current_turn_entity"
