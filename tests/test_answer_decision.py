from src.hybrid.answer_decision import (
    AnswerDecisionPlanner,
    AnswerDecisionType,
    render_clarification,
)
from src.hybrid.query_plan import QueryIntent, QueryPlan


def plan(intent=None, *, mutative=False):
    return QueryPlan(
        question="pregunta",
        normalized_question="pregunta",
        intent=intent,
        target_entity_types=(),
        requires_entity_resolution=True,
        requires_graph_context=intent != QueryIntent.SCREEN_PURPOSE,
        requires_semantic_evidence=intent == QueryIntent.SCREEN_PURPOSE,
        mutative_action=mutative,
    )


def test_ambiguity_requires_clarification_before_any_answer_path():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(QueryIntent.LOCATE_FIELD),
        evidence_selection={
            "status": "clarification_required",
            "reason": "entity_resolution_ambiguous",
        },
        deterministic_plan={"supported": True, "confidence": "high"},
        has_context=True,
        has_sources=True,
    )

    assert decision.decision == AnswerDecisionType.CLARIFICATION
    assert decision.reason == "entity_resolution_ambiguous"
    assert decision.confidence == "high"


def test_mutative_policy_abstention_precedes_generation():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(QueryIntent.MUTATIVE_ACTION, mutative=True),
        evidence_selection={"status": "selected", "reason": "mutative_evidence"},
        deterministic_plan={"supported": False},
        has_context=True,
        has_sources=True,
    )

    assert decision.decision == AnswerDecisionType.ABSTENTION
    assert decision.reason == "mutative_action_policy"
    assert decision.confidence == "high"


def test_supported_structural_plan_becomes_deterministic_answer():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(QueryIntent.LOCATE_SCREEN),
        evidence_selection={"status": "selected", "reason": "locate_screen"},
        deterministic_plan={"supported": True, "confidence": "high"},
        has_context=True,
        has_sources=True,
    )

    assert decision.decision == AnswerDecisionType.DETERMINISTIC_ANSWER
    assert decision.reason == "deterministic_structural_answer"


def test_approved_semantic_plan_is_still_a_deterministic_decision():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(QueryIntent.SCREEN_PURPOSE),
        evidence_selection={"status": "selected", "reason": "screen_purpose"},
        deterministic_plan={
            "supported": True,
            "confidence": "high",
            "answer_mode": "deterministic_semantic",
        },
        has_context=True,
        has_sources=True,
    )

    assert decision.decision == AnswerDecisionType.DETERMINISTIC_ANSWER
    assert decision.reason == "approved_semantic_answer"


def test_selected_context_without_deterministic_answer_uses_grounded_llm():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(None),
        evidence_selection={"status": "selected", "reason": "bounded_generic"},
        deterministic_plan={"supported": False},
        has_context=True,
        has_sources=True,
    )

    assert decision.decision == AnswerDecisionType.GROUNDED_LLM
    assert decision.reason == "grounded_context_available"
    assert decision.confidence == "medium"


def test_insufficient_selection_abstains_without_generation():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(QueryIntent.LIST_COLUMNS),
        evidence_selection={"status": "insufficient", "reason": "list_columns_insufficient"},
        deterministic_plan={"supported": False},
        has_context=False,
        has_sources=False,
    )

    assert decision.decision == AnswerDecisionType.ABSTENTION
    assert decision.reason == "list_columns_insufficient"


def test_clarification_renderer_never_exposes_canonical_ids():
    answer = render_clarification(
        [
            {
                "canonical_id": "field:secret-a",
                "entity_type": "field",
                "safe_label": "RUC",
                "route": None,
            },
            {
                "canonical_id": "field:secret-b",
                "entity_type": "field",
                "safe_label": "RUC",
                "route": None,
            },
        ]
    )

    assert '"RUC"' in answer
    assert "pantalla" in answer
    assert "field:" not in answer


def test_supported_mutative_guidance_stays_deterministic_before_policy_abstention():
    planner = AnswerDecisionPlanner()
    decision = planner.decide(
        plan(QueryIntent.MUTATIVE_ACTION, mutative=True),
        evidence_selection={"status": "selected", "reason": "mutative_evidence"},
        deterministic_plan={"supported": True, "confidence": "high"},
        has_context=True,
        has_sources=True,
        policy_abstention=True,
    )

    assert decision.decision == AnswerDecisionType.DETERMINISTIC_ANSWER
    assert decision.reason == "deterministic_structural_answer"
