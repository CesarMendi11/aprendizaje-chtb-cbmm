from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.api.admin_knowledge_serializers import (
    comparable_from_historical,
    comparable_structure_hash,
    counters,
    current_structure,
    historical_evidence,
    safe_history_payload,
    semantic_projection,
    tolerant_proposal_summary,
    tree_screen,
)
from src.api.dependencies import get_admin_read_session
from src.api.routes.semantic_review import AdminSemanticApiError
from src.api.schemas.admin_knowledge import (
    AdminErpSummary,
    AdminModuleSummary,
    AdminReviewHistoryItem,
    AdminScreenDetail,
    AdminScreenListItem,
    AdminScreenListResponse,
    AdminVersionSummary,
    KnowledgeTreeErp,
    KnowledgeTreeModule,
    KnowledgeTreeResponse,
    ProposalContext,
    ScreenNavigation,
    ScreenReviewContextResponse,
    ScreenSemanticState,
    TraceabilitySummary,
)
from src.database.enums import KnowledgeVersionStatus
from src.database.repositories.admin_knowledge_repository import AdminKnowledgeRepository

router = APIRouter(tags=["local knowledge console (provisional)"])
SessionDependency = Annotated[Session, Depends(get_admin_read_session)]


def _sort_key(name: str, canonical_id: str) -> tuple[str, str]:
    return " ".join(name.casefold().split()), canonical_id


def _check_scope(
    repository: AdminKnowledgeRepository,
    erp_id: str | None,
    version_id: uuid.UUID | None,
) -> None:
    if erp_id is not None and not repository.erp_exists(erp_id):
        raise AdminSemanticApiError(404, "ERPNotFoundError", "not_found", "ERP no encontrado.")
    if version_id is not None:
        version = repository.version(version_id)
        if version is None:
            raise AdminSemanticApiError(
                404, "KnowledgeVersionNotFoundError", "not_found", "Versión no encontrada."
            )
        if erp_id is not None and version.erp_id != erp_id:
            raise AdminSemanticApiError(
                404, "KnowledgeVersionNotFoundError", "not_found", "Versión no encontrada."
            )
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise AdminSemanticApiError(
                409,
                "KnowledgeVersionNotActiveError",
                "inactive_version",
                "La versión solicitada no está activa.",
            )


def _tree(
    session: Session,
    *,
    erp_id: str | None,
    knowledge_version_id: uuid.UUID | None,
    include_empty_modules: bool,
    semantic_status: ScreenSemanticState | None,
    search: str | None,
) -> KnowledgeTreeResponse:
    repository = AdminKnowledgeRepository(session)
    _check_scope(repository, erp_id, knowledge_version_id)
    erps = []
    needle = " ".join(search.casefold().split()) if search else None
    for snapshot in repository.snapshots(erp_id=erp_id, knowledge_version_id=knowledge_version_id):
        module_by_id = {module.module_id: module for module in snapshot.modules if module.value}
        actions = defaultdict(list)
        for rows in snapshot.proposals.values():
            for proposal, _count in rows:
                actions[proposal.id].extend(proposal.review_actions)
        grouped = defaultdict(list)
        unassigned = []
        for screen in snapshot.screens:
            summary = tree_screen(
                screen,
                snapshot.proposals.get(screen.item.id, ()),
                actions,
            )
            if semantic_status is not None and summary.semantic_state != semantic_status:
                continue
            searchable = f"{summary.title} {summary.route} {summary.screen_id}".casefold()
            if needle and needle not in searchable:
                continue
            if screen.module_id in module_by_id and screen.diagnostic is None:
                grouped[screen.module_id].append(summary)
            else:
                unassigned.append(summary)
        modules = []
        ordered_modules = sorted(
            snapshot.modules,
            key=lambda value: _sort_key(value.name or "", value.module_id),
        )
        warnings = []
        for order, module in enumerate(ordered_modules):
            if module.diagnostic:
                warnings.append(module.diagnostic)
            screens = tuple(
                sorted(
                    grouped[module.module_id],
                    key=lambda value: _sort_key(value.title or "", value.screen_id),
                )
            )
            if screens or include_empty_modules:
                modules.append(
                    KnowledgeTreeModule(
                        module_id=module.module_id,
                        name=module.name,
                        route=module.route,
                        available=module.diagnostic is None,
                        diagnostic=module.diagnostic,
                        order=order,
                        screens=screens,
                        counters=counters(screens),
                    )
                )
        unassigned_sorted = tuple(
            sorted(unassigned, key=lambda value: _sort_key(value.title or "", value.screen_id))
        )
        all_screens = (
            tuple(screen for module in modules for screen in module.screens) + unassigned_sorted
        )
        erps.append(
            KnowledgeTreeErp(
                erp_id=snapshot.erp.id,
                name=snapshot.erp.name,
                slug=snapshot.erp.slug,
                active_knowledge_version_id=str(snapshot.version.id),
                knowledge_version=snapshot.version.knowledge_version,
                modules=tuple(modules),
                unassigned_screens=unassigned_sorted,
                warnings=tuple(warnings),
                counters=counters(all_screens),
            )
        )
    return KnowledgeTreeResponse(erps=tuple(erps))


@router.get(
    "/knowledge-tree",
    response_model=KnowledgeTreeResponse,
    summary="Navegar la jerarquía canónica aprobada de versiones activas",
    description=(
        "Jerarquía para una consola administrativa local provisional. La API está "
        "desactivada por defecto y no constituye autenticación ni RBAC definitivo; "
        "reviewer_identity_verified siempre es false cuando aplica."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "erps": [
                            {
                                "erp_id": "erp:synthetic",
                                "name": "ERP sintético",
                                "slug": "synthetic",
                                "active_knowledge_version_id": (
                                    "00000000-0000-4000-8000-000000000001"
                                ),
                                "knowledge_version": "synthetic-v1",
                                "modules": [],
                                "unassigned_screens": [],
                                "warnings": [],
                                "counters": {
                                    "total_screens": 0,
                                    "no_proposal": 0,
                                    "pending_review": 0,
                                    "approved": 0,
                                    "corrected": 0,
                                    "rejected": 0,
                                    "unavailable": 0,
                                    "warnings_total": 0,
                                },
                            }
                        ]
                    }
                }
            }
        }
    },
)
def knowledge_tree(
    session: SessionDependency,
    erp_id: str | None = Query(default=None, max_length=160),
    knowledge_version_id: uuid.UUID | None = None,
    include_empty_modules: bool = False,
    semantic_status: ScreenSemanticState | None = None,
    search: str | None = Query(default=None, min_length=1, max_length=200),
) -> KnowledgeTreeResponse:
    return _tree(
        session,
        erp_id=erp_id,
        knowledge_version_id=knowledge_version_id,
        include_empty_modules=include_empty_modules,
        semantic_status=semantic_status,
        search=search,
    )


@router.get(
    "/screens",
    response_model=AdminScreenListResponse,
    summary="Buscar pantallas canónicas con paginación SQL",
    description=(
        "Listado de una consola administrativa local provisional, desactivada por defecto. "
        "No implementa autenticación ni RBAC definitivo."
    ),
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "items": [],
                        "total": 0,
                        "limit": 50,
                        "offset": 0,
                        "next_offset": None,
                    }
                }
            }
        }
    },
)
def list_screens(
    session: SessionDependency,
    erp_id: str | None = Query(default=None, max_length=160),
    erp: str | None = Query(default=None, max_length=160, deprecated=True),
    module_id: str | None = Query(default=None, max_length=200),
    module: str | None = Query(default=None, max_length=200, deprecated=True),
    semantic_status: ScreenSemanticState | None = None,
    text: str | None = Query(default=None, min_length=1, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminScreenListResponse:
    selected_erp = erp_id or erp
    selected_module = module_id or module
    repository = AdminKnowledgeRepository(session)
    if selected_erp and not repository.erp_exists(selected_erp):
        raise AdminSemanticApiError(404, "ERPNotFoundError", "not_found", "ERP no encontrado.")
    rows, total = repository.list_screen_page(
        erp_id=selected_erp,
        module_id=selected_module,
        text=text,
        semantic_status=str(semantic_status) if semantic_status else None,
        limit=limit,
        offset=offset,
    )
    items = tuple(
        AdminScreenListItem(
            erp_id=row.erp.id,
            knowledge_version_id=str(row.version.id),
            module_id=row.module.module_id if row.module else None,
            module_name=row.module.name if row.module else None,
            screen=tree_screen(row.screen, row.proposals, row.semantic_actions),
        )
        for row in rows
    )
    return AdminScreenListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(items) if offset + len(items) < total else None,
    )


@router.get(
    "/screens/{screen_id}/review-context",
    response_model=ScreenReviewContextResponse,
    summary="Reunir contexto administrativo seguro de una pantalla",
    description=(
        "Contexto read-only para una consola local provisional. Separa estructura "
        "canónica actual de snapshots históricos; no ofrece autenticación ni RBAC "
        "definitivo y reviewer_identity_verified es false."
    ),
    responses={
        200: {
            "description": "Contexto administrativo sintético.",
            "content": {
                "application/json": {
                    "example": {
                        "erp": {
                            "erp_id": "erp:synthetic",
                            "name": "ERP sintético",
                            "slug": "synthetic",
                        },
                        "semantic_state": "no_proposal",
                        "semantic_proposals": [],
                        "review_history": [],
                        "reviewer_identity_verified": False,
                    }
                }
            },
        }
    },
)
def review_context(screen_id: str, session: SessionDependency) -> ScreenReviewContextResponse:
    repository = AdminKnowledgeRepository(session)
    found = repository.screen_snapshot(screen_id)
    if found is None:
        raise AdminSemanticApiError(
            404, "ScreenNotFoundError", "not_found", "Pantalla no encontrada."
        )
    snapshot, screen = found
    rows = snapshot.proposals.get(screen.item.id, ())
    proposals = [proposal for proposal, _ in rows]
    actions = [action for proposal in proposals for action in proposal.review_actions]
    by_proposal = defaultdict(list)
    for action in actions:
        by_proposal[action.semantic_proposal_id].append(action)
    projection = semantic_projection(rows, by_proposal)
    modules = {module.module_id: module for module in snapshot.modules if module.value}
    module_data = modules.get(screen.module_id) if screen.diagnostic is None else None
    structure_items = repository.screen_structure_snapshot(
        snapshot.version.id,
        screen,
        module_data,
    )
    payloads = repository.effective_payloads(structure_items)
    structure = current_structure(screen, module_data, structure_items, payloads)
    contexts = []
    for proposal, count in rows:
        proposal_projection = semantic_projection(((proposal, count),), by_proposal)
        evidence = historical_evidence(proposal)
        diagnostic = proposal_projection.diagnostic or evidence.diagnostic
        historical_comparable = comparable_from_historical(evidence)
        historical_structure_hash = (
            comparable_structure_hash(historical_comparable)
            if historical_comparable is not None
            else None
        )
        matches = historical_structure_hash == structure.current_structure_hash
        if evidence.evidence_available and not matches:
            diagnostic = diagnostic or "La evidencia histórica difiere de la estructura actual."
        contexts.append(
            ProposalContext(
                summary=tolerant_proposal_summary(proposal, count, proposal_projection.payload),
                effective_payload=proposal_projection.payload,
                evidence=evidence,
                historical_structure_hash=historical_structure_hash,
                current_structure_hash=structure.current_structure_hash,
                evidence_matches_current_structure=matches,
                diagnostic=diagnostic,
            )
        )
    active = next(
        (
            context
            for context in contexts
            if projection.active and context.summary.semantic_id == projection.active.semantic_id
        ),
        None,
    )
    history_values = []
    semantic_ids = {proposal.id: proposal.semantic_id for proposal in proposals}
    for action in actions:
        corrected_payload, diagnostic = safe_history_payload(action)
        history_values.append(
            AdminReviewHistoryItem(
                semantic_id=semantic_ids[action.semantic_proposal_id],
                action=str(action.action),
                previous_status=action.previous_status,
                new_status=action.new_status,
                reason=action.review_notes,
                reviewer_id=action.reviewer_subject,
                corrected_payload=corrected_payload,
                created_at=action.created_at,
                diagnostic=diagnostic,
            )
        )
    history = tuple(history_values)
    peers = repository.navigation_ids(snapshot.version.id, screen.module_id)
    position = peers.index(screen.screen_id)
    return ScreenReviewContextResponse(
        erp=AdminErpSummary(erp_id=snapshot.erp.id, name=snapshot.erp.name, slug=snapshot.erp.slug),
        version=AdminVersionSummary(
            knowledge_version_id=str(snapshot.version.id),
            knowledge_version=snapshot.version.knowledge_version,
            status=str(snapshot.version.status),
        ),
        module=(
            AdminModuleSummary(
                module_id=module_data.module_id, name=module_data.name, route=module_data.route
            )
            if module_data is not None
            else None
        ),
        screen=AdminScreenDetail(
            screen_id=screen.screen_id,
            title=screen.title,
            route=screen.route,
            structural_review_status=screen.item.current_review_status,
            structural_available=screen.diagnostic is None,
            diagnostic=screen.diagnostic,
        ),
        structural_evidence=structure,
        semantic_proposals=tuple(contexts),
        active_proposal=active,
        review_history=history,
        effective_payload=projection.payload,
        traceability=TraceabilitySummary(
            proposal_count=len(proposals),
            review_action_count=len(actions),
            evidence_available=structure.evidence_available,
            evidence_ids=structure.evidence_ids,
            warnings=structure.warnings,
        ),
        semantic_state=(ScreenSemanticState.UNAVAILABLE if screen.diagnostic else projection.state),
        navigation=ScreenNavigation(
            previous_screen_id=peers[position - 1] if position else None,
            next_screen_id=peers[position + 1] if position + 1 < len(peers) else None,
            module_screen_position=position + 1,
            module_screen_total=len(peers),
        ),
    )
