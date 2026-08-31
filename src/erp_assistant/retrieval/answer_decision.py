from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from .query_plan import QueryPlan


class AnswerDecisionType(StrEnum):
    DETERMINISTIC_ANSWER = "DETERMINISTIC_ANSWER"
    GROUNDED_LLM = "GROUNDED_LLM"
    CLARIFICATION = "CLARIFICATION"
    ABSTENTION = "ABSTENTION"


@dataclass(frozen=True)
class AnswerDecision:
    decision: AnswerDecisionType
    reason: str
    intent: str | None
    confidence: str

    def as_dict(self) -> dict[str, object]:
        return {
            "decision": str(self.decision),
            "reason": self.reason,
            "intent": self.intent,
            "confidence": self.confidence,
        }


class AnswerDecisionPlanner:
    """Choose the answer path without generating user-facing prose.

    The decision layer consumes only already-governed retrieval/evidence state.
    It does not retrieve new facts, resolve entities, or let the LLM choose
    between ambiguous canonical candidates.
    """

    def decide(
        self,
        query_plan: QueryPlan,
        *,
        evidence_selection: Mapping[str, object] | None,
        deterministic_plan: Mapping[str, object] | None,
        has_context: bool,
        has_sources: bool,
        policy_abstention: bool = False,
    ) -> AnswerDecision:
        selection = evidence_selection or {}
        status = str(selection.get("status") or "").strip()
        deterministic_plan = deterministic_plan or {}
        intent = str(query_plan.intent) if query_plan.intent is not None else None

        if status == "clarification_required":
            return AnswerDecision(
                decision=AnswerDecisionType.CLARIFICATION,
                reason=str(selection.get("reason") or "clarification_required"),
                intent=intent,
                confidence="high",
            )

        if deterministic_plan.get("supported"):
            reason = (
                "approved_semantic_answer"
                if deterministic_plan.get("answer_mode") == "deterministic_semantic"
                else "deterministic_structural_answer"
            )
            return AnswerDecision(
                decision=AnswerDecisionType.DETERMINISTIC_ANSWER,
                reason=reason,
                intent=intent,
                confidence=str(deterministic_plan.get("confidence") or "high"),
            )

        if query_plan.mutative_action or policy_abstention:
            return AnswerDecision(
                decision=AnswerDecisionType.ABSTENTION,
                reason="mutative_action_policy",
                intent=intent,
                confidence="high",
            )

        # Mocked/legacy callers may not yet provide evidence_selection. In that
        # case the existing context/source contract remains the compatibility
        # signal until /api/chat vNext owns the richer contract directly.
        if status and status != "selected":
            return AnswerDecision(
                decision=AnswerDecisionType.ABSTENTION,
                reason=str(selection.get("reason") or "insufficient_evidence"),
                intent=intent,
                confidence="low",
            )

        if not has_context or not has_sources:
            return AnswerDecision(
                decision=AnswerDecisionType.ABSTENTION,
                reason="insufficient_evidence",
                intent=intent,
                confidence="low",
            )

        return AnswerDecision(
            decision=AnswerDecisionType.GROUNDED_LLM,
            reason="grounded_context_available",
            intent=intent,
            confidence="medium",
        )

    @staticmethod
    def generator_abstention(query_plan: QueryPlan) -> AnswerDecision:
        return AnswerDecision(
            decision=AnswerDecisionType.ABSTENTION,
            reason="generator_abstained",
            intent=str(query_plan.intent) if query_plan.intent is not None else None,
            confidence="low",
        )


def render_clarification(
    candidates: Sequence[Mapping[str, object]],
) -> str:
    """Render a safe clarification without exposing canonical IDs.

    Candidate identities remain in diagnostics for later controlled multi-turn
    resolution; user-facing text contains only safe labels/routes.
    """

    rows = [
        row
        for row in candidates
        if str(row.get("safe_label") or "").strip()
    ]
    labels: list[str] = []
    for row in rows:
        label = str(row.get("safe_label") or "").strip()
        if label and label not in labels:
            labels.append(label)

    if len(labels) == 1:
        return (
            f'Encontré varias coincidencias para "{labels[0]}". '
            "Indícame la pantalla o el módulo al que te refieres para poder elegir la correcta."
        )

    if 1 < len(labels) <= 3:
        choices = ", ".join(f'"{label}"' for label in labels)
        return f"Encontré varias coincidencias: {choices}. Indícame cuál quieres consultar."

    return (
        "Encontré varias coincidencias posibles. "
        "Indícame la pantalla, módulo o elemento concreto al que te refieres."
    )
