from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from pydantic import ValidationError

from src.analysis.schemas import (
    ColumnEvidence,
    ControlEvidence,
    EventEvidence,
    FieldEvidence,
    ModuleEvidence,
    ScreenEvidencePackage,
    ScreenPurposeInference,
    TableEvidence,
    TransitionEvidence,
    UIStateEvidence,
)
from src.api.schemas.admin_knowledge import (
    AdminEvidence,
    AdminProposalSummary,
    ComparableScreenStructure,
    HistoricalProposalEvidence,
    KnowledgeCounters,
    KnowledgeTreeScreen,
    ScreenSemanticState,
)
from src.database.models import KnowledgeItem, SemanticProposal, SemanticReviewAction
from src.database.repositories.admin_knowledge_repository import EffectiveModule, EffectiveScreen
from src.database.services.semantic_payloads import canonical_json_hash
from src.knowledge.canonical.models import Evidence as CanonicalEvidence


@dataclass(frozen=True)
class SemanticProjection:
    state: ScreenSemanticState
    active: SemanticProposal | None
    payload: ScreenPurposeInference | None
    diagnostic: str | None = None


def _valid_payload(value) -> ScreenPurposeInference | None:
    try:
        return ScreenPurposeInference.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        return None


def semantic_projection(
    rows: tuple[tuple[SemanticProposal, int], ...],
    actions: dict | None = None,
) -> SemanticProjection:
    if not rows:
        return SemanticProjection(ScreenSemanticState.NO_PROPOSAL, None, None)
    latest = rows[-1][0]
    tied = [proposal for proposal, _ in rows if proposal.created_at == latest.created_at]
    if len({(str(p.semantic_type), str(p.current_review_status)) for p in tied}) > 1:
        return SemanticProjection(
            ScreenSemanticState.MIXED,
            latest,
            None,
            "Existen propuestas vigentes incompatibles con la misma prioridad.",
        )
    payload_value = latest.source_payload
    if str(latest.current_review_status) == "corrected":
        proposal_actions = (actions or {}).get(latest.id, ())
        correction = None
        for action in reversed(proposal_actions):
            if str(action.action) == "reset_to_pending":
                break
            if str(action.action) == "correct" and action.corrected_payload is not None:
                correction = _valid_payload(action.corrected_payload)
                if correction is None:
                    return SemanticProjection(
                        ScreenSemanticState.UNAVAILABLE,
                        latest,
                        None,
                        "La corrección semántica persistida no cumple el esquema esperado.",
                    )
                break
        if correction is None:
            return SemanticProjection(
                ScreenSemanticState.UNAVAILABLE,
                latest,
                None,
                "La propuesta corregida no tiene una acción de corrección válida.",
            )
        return SemanticProjection(ScreenSemanticState.CORRECTED, latest, correction)
    payload = _valid_payload(payload_value)
    if payload is None:
        return SemanticProjection(
            ScreenSemanticState.UNAVAILABLE,
            latest,
            None,
            "El payload semántico persistido no cumple el esquema esperado.",
        )
    return SemanticProjection(
        ScreenSemanticState(str(latest.current_review_status)), latest, payload
    )


def tree_screen(
    screen: EffectiveScreen,
    rows: tuple[tuple[SemanticProposal, int], ...],
    actions: dict | None = None,
) -> KnowledgeTreeScreen:
    projection = semantic_projection(rows, actions)
    state = ScreenSemanticState.UNAVAILABLE if screen.diagnostic else projection.state
    evidence = historical_evidence(projection.active) if projection.active else None
    return KnowledgeTreeScreen(
        screen_id=screen.screen_id,
        title=screen.title,
        route=screen.route,
        structural_review_status=screen.item.current_review_status,
        structural_available=screen.diagnostic is None,
        diagnostic=screen.diagnostic or projection.diagnostic,
        semantic_state=state,
        proposal_count=len(rows),
        pending_count=sum(
            str(proposal.current_review_status) == "pending_review" for proposal, _ in rows
        ),
        latest_semantic_id=projection.active.semantic_id if projection.active else None,
        latest_semantic_status=(
            projection.active.current_review_status if projection.active else None
        ),
        capabilities_count=(
            len(projection.payload.supported_capabilities) if projection.payload else None
        ),
        evidence_available=screen.diagnostic is None,
        warnings_count=(len(evidence.warnings) if evidence else 0) + bool(screen.diagnostic),
    )


def tolerant_proposal_summary(proposal, action_count: int, payload):
    return AdminProposalSummary(
        semantic_id=proposal.semantic_id,
        semantic_type=str(proposal.semantic_type),
        current_review_status=proposal.current_review_status,
        review_revision=proposal.review_revision,
        erp_id=proposal.knowledge_version.erp_id,
        knowledge_version_id=str(proposal.knowledge_version_id),
        screen_id=proposal.screen_knowledge_item.canonical_id,
        subject_title=proposal.screen_knowledge_item.title,
        purpose_summary=payload.purpose_summary if payload else None,
        generation_model=proposal.generation_model,
        prompt_version=proposal.prompt_version,
        evidence_hash=proposal.evidence_hash,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        review_action_count=action_count,
        diagnostic=(
            None if payload else "El payload semántico no está disponible de forma segura."
        ),
    )


def historical_evidence(proposal: SemanticProposal | None) -> HistoricalProposalEvidence:
    if proposal is None:
        return HistoricalProposalEvidence(
            evidence_available=False,
            diagnostic="No existe snapshot histórico de evidencia.",
            evidence_hash="",
        )
    try:
        package = ScreenEvidencePackage.model_validate(
            {**proposal.evidence_payload, "evidence_hash": proposal.evidence_hash}
        )
    except (ValidationError, TypeError, ValueError):
        return HistoricalProposalEvidence(
            evidence_available=False,
            diagnostic="La evidencia histórica no cumple el esquema esperado.",
            evidence_hash=proposal.evidence_hash,
        )
    return HistoricalProposalEvidence(
        evidence_available=True,
        screen_id=package.screen_id,
        screen_title=package.screen_title,
        screen_route=package.screen_route,
        module=package.module,
        fields=tuple(package.fields),
        controls=tuple(package.controls),
        tables=tuple(package.tables),
        ui_states=tuple(package.ui_states),
        events=tuple(package.events),
        transitions=tuple(package.transitions),
        evidence_ids=tuple(package.evidence_ids),
        warnings=tuple(package.warnings),
        evidence_hash=proposal.evidence_hash,
    )


def comparable_from_current(evidence: AdminEvidence) -> ComparableScreenStructure:
    return ComparableScreenStructure(
        screen_id=evidence.screen_id,
        screen_title=evidence.screen_title,
        screen_route=evidence.screen_route,
        module=evidence.module,
        fields=evidence.fields,
        controls=evidence.controls,
        tables=evidence.tables,
        ui_states=evidence.ui_states,
        events=evidence.events,
        transitions=evidence.transitions,
        evidence_ids=evidence.evidence_ids,
    )


def comparable_from_historical(
    evidence: HistoricalProposalEvidence,
) -> ComparableScreenStructure | None:
    if not evidence.evidence_available or evidence.screen_id is None:
        return None
    return ComparableScreenStructure(
        screen_id=evidence.screen_id,
        screen_title=evidence.screen_title,
        screen_route=evidence.screen_route,
        module=evidence.module,
        fields=evidence.fields,
        controls=evidence.controls,
        tables=evidence.tables,
        ui_states=evidence.ui_states,
        events=evidence.events,
        transitions=evidence.transitions,
        evidence_ids=evidence.evidence_ids,
    )


def comparable_structure_hash(value: ComparableScreenStructure) -> str:
    return canonical_json_hash(value.model_dump(mode="json"))


def current_structure(
    screen: EffectiveScreen,
    module: EffectiveModule | None,
    items: tuple[KnowledgeItem, ...],
    payloads: dict,
) -> AdminEvidence:
    warnings: list[str] = []
    if screen.diagnostic:
        warnings.append(screen.diagnostic)
    pairs = []
    for item in items:
        payload = payloads.get(item.id)
        if not isinstance(payload, dict):
            warnings.append(
                f"Elemento estructural omitido: {item.entity_type}:{item.canonical_id}."
            )
            payload = {}
        pairs.append((item, payload))
    related = [
        (item, payload) for item, payload in pairs if payload.get("screen_id") == screen.screen_id
    ]

    def invalid(item: KnowledgeItem) -> None:
        warnings.append(f"Elemento estructural omitido: {item.entity_type}:{item.canonical_id}.")

    def validated(entity_type, factory):
        result = []
        for item, payload in related:
            if item.entity_type != entity_type:
                continue
            try:
                result.append(factory(item, payload))
            except (ValidationError, TypeError, ValueError, KeyError):
                invalid(item)
        return tuple(result)

    for evidence_item, evidence_payload in pairs:
        if evidence_item.entity_type != "evidence":
            continue
        try:
            CanonicalEvidence.model_validate(evidence_payload)
        except (ValidationError, TypeError, ValueError):
            invalid(evidence_item)

    tables = []
    selected_columns = []
    for item, payload in related:
        if item.entity_type != "table":
            continue
        columns = []
        for column, column_payload in pairs:
            if (
                column.entity_type != "table_column"
                or column_payload.get("table_id") != item.canonical_id
            ):
                continue
            try:
                if not isinstance(column_payload.get("name"), str):
                    raise ValueError
                columns.append(
                    ColumnEvidence(
                        column_id=column.canonical_id,
                        label=column_payload["name"],
                    )
                )
                selected_columns.append((column, column_payload))
            except (ValidationError, TypeError, ValueError):
                invalid(column)
        try:
            if payload.get("name") is not None and not isinstance(payload["name"], str):
                raise ValueError
            tables.append(
                TableEvidence(
                    table_id=item.canonical_id,
                    name=payload.get("name") or "Tabla",
                    columns=columns,
                )
            )
        except (ValidationError, TypeError, ValueError):
            invalid(item)

    fields = validated(
        "field",
        lambda item, payload: FieldEvidence(
            field_id=item.canonical_id,
            label=payload["label"],
            input_type=payload.get("input_type"),
            required=payload.get("required", False),
            readonly=payload.get("readonly", False),
        ),
    )
    controls = validated(
        "control",
        lambda item, payload: ControlEvidence(
            control_id=item.canonical_id,
            label=payload["label"],
            control_type=payload.get("control_type"),
            mutative=payload.get("mutative", False),
            safety_decision=payload.get("safety_decision"),
        ),
    )
    states = validated(
        "ui_state",
        lambda item, payload: UIStateEvidence(
            state_id=item.canonical_id,
            title=payload["title"],
            depth=payload.get("depth"),
        ),
    )
    events = validated(
        "event",
        lambda item, payload: EventEvidence(
            event_id=item.canonical_id,
            label=payload["label"],
            category=payload["category"],
            policy_decision=payload["policy_decision"],
            mutative=payload.get("mutative", False),
        ),
    )
    state_ids = {value.state_id for value in states}
    raw_state_ids = {
        item.canonical_id for item, _payload in related if item.entity_type == "ui_state"
    }
    control_ids = {value.control_id for value in controls}
    transitions = []
    for item, payload in pairs:
        if item.entity_type != "transition":
            continue
        source = payload.get("source_state_id")
        target = payload.get("target_state_id")
        trigger = payload.get("trigger_control_id")
        if source not in raw_state_ids and target not in raw_state_ids:
            continue
        if source not in state_ids and target not in state_ids:
            invalid(item)
            continue
        if trigger is not None and trigger not in control_ids:
            invalid(item)
            continue
        try:
            transitions.append(
                TransitionEvidence(
                    transition_id=item.canonical_id,
                    category=payload["category"],
                    source_state_id=source,
                    target_state_id=target,
                    trigger_control_id=trigger,
                )
            )
        except (ValidationError, TypeError, ValueError, KeyError):
            invalid(item)
    selected_pairs = [
        pair
        for pair in pairs
        if pair[0].id == screen.item.id
        or (module is not None and pair[0].id == module.item.id)
        or pair in related
        or pair in selected_columns
    ]
    evidence_ids = tuple(
        sorted(
            {
                str(evidence_id)
                for _item, payload in selected_pairs
                for evidence_id in payload.get("evidence_ids", [])
                if isinstance(evidence_id, str) and evidence_id.strip()
            }
        )
    )
    evidence = AdminEvidence(
        evidence_available=screen.diagnostic is None,
        diagnostic=screen.diagnostic,
        screen_id=screen.screen_id,
        screen_title=screen.title,
        screen_route=screen.route,
        module=(
            ModuleEvidence(module_id=module.module_id, name=module.name)
            if module and module.name
            else None
        ),
        fields=fields,
        controls=controls,
        tables=tuple(tables),
        ui_states=states,
        events=events,
        transitions=tuple(transitions),
        evidence_ids=evidence_ids,
        warnings=tuple(warnings),
        current_structure_hash="0" * 64,
    )
    fingerprint = comparable_structure_hash(comparable_from_current(evidence))
    return evidence.model_copy(update={"current_structure_hash": fingerprint})


def safe_history_payload(action: SemanticReviewAction):
    if action.corrected_payload is None:
        return None, None
    payload = _valid_payload(action.corrected_payload)
    if payload is None:
        return None, "El payload histórico corregido no cumple el esquema esperado."
    return payload, None


def counters(screens: Iterable[KnowledgeTreeScreen]) -> KnowledgeCounters:
    materialized = tuple(screens)
    states = Counter(screen.semantic_state for screen in materialized)
    return KnowledgeCounters(
        total_screens=len(materialized),
        no_proposal=states[ScreenSemanticState.NO_PROPOSAL],
        pending_review=states[ScreenSemanticState.PENDING_REVIEW],
        approved=states[ScreenSemanticState.APPROVED],
        corrected=states[ScreenSemanticState.CORRECTED],
        rejected=states[ScreenSemanticState.REJECTED],
        unavailable=states[ScreenSemanticState.UNAVAILABLE] + states[ScreenSemanticState.MIXED],
        warnings_total=sum(screen.warnings_count for screen in materialized),
    )
