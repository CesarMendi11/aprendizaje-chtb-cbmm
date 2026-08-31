from __future__ import annotations

from collections.abc import Iterable

from erp_assistant.structural.services.removal_reconciliation_review_service import (
    RemovalReconciliationReviewService,
)


def resolve_all_removals(
    session,
    candidate_version_id,
    *,
    confirmed_remove: Iterable[tuple[str, str]] = (),
    reviewer: str = "reviewer:test",
):
    service = RemovalReconciliationReviewService(session)
    state = service.prepare(candidate_version_id)
    removed = set(confirmed_remove)
    for decision in state.decisions:
        kwargs = {
            "reviewer": reviewer,
            "reason": "Decisión humana de prueba.",
            "expected_revision": decision.review_revision,
        }
        key = (decision.entity_type, decision.canonical_id)
        if key in removed:
            service.confirm_remove(decision.id, **kwargs)
        else:
            service.confirm_retain(decision.id, **kwargs)
    return service.get(candidate_version_id)
