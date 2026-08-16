from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from src.api.dependencies import get_admin_read_session, get_semantic_review_session
from src.api.pipeline_job_serializers import pipeline_job_detail, pipeline_job_summary
from src.api.schemas.pipeline_jobs import (
    CanonicalBuildPipelineJobCreateRequest,
    CanonicalImportPipelineJobCreateRequest,
    CanonicalMergePipelineJobCreateRequest,
    CanonicalReconciliationPipelineJobCreateRequest,
    ChromaSyncPipelineJobCreateRequest,
    CrawlPipelineJobCreateRequest,
    Neo4jSyncPipelineJobCreateRequest,
    PipelineJobDetail,
    PipelineJobListResponse,
    SemanticInferencePipelineJobCreateRequest,
    SemanticSyncPipelineJobCreateRequest,
)
from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    SyncStatus,
    SyncTarget,
)
from src.database.models import KnowledgeVersionRecord
from src.database.repositories import (
    KnowledgeRepository,
    PipelineJobRepository,
    SyncJobRepository,
)
from src.database.services import (
    ModuleSubtreeResolutionError,
    ModuleSubtreeResolver,
    PipelineJobService,
    RemovalReconciliationReviewError,
    RemovalReconciliationReviewNotPreparedError,
    RemovalReconciliationReviewService,
    ScreenScopeResolutionError,
    ScreenScopeResolver,
)
from src.knowledge.canonical.enums import ReviewStatus

router = APIRouter(prefix="/pipeline-jobs", tags=["admin pipeline jobs (provisional)"])
SessionDependency = Annotated[Session, Depends(get_admin_read_session)]
WriteSessionDependency = Annotated[Session, Depends(get_semantic_review_session)]


def _single_active_version(session: Session):
    active = [
        version
        for version in KnowledgeRepository(session).list_versions()
        if version.status == KnowledgeVersionStatus.ACTIVE
    ]
    if len(active) != 1:
        raise HTTPException(
            status_code=409,
            detail="Se requiere exactamente una versión ACTIVE para esta operación.",
        )
    return active[0]


def _structural_sync_target(kind: PipelineJobKind) -> SyncTarget | None:
    return {
        PipelineJobKind.NEO4J_SYNC: SyncTarget.NEO4J,
        PipelineJobKind.CHROMA_SYNC: SyncTarget.CHROMADB,
    }.get(kind)


def _queue_active_projection_job(
    *,
    request: Request,
    session: Session,
    kind: PipelineJobKind,
    parameters: dict,
):
    version = _single_active_version(session)
    existing = PipelineJobRepository(session).find_active_projection_job(
        kind=kind,
        knowledge_version_id=version.id,
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ya existe un job {kind.value} en cola o ejecución para la versión ACTIVE "
                f"{version.knowledge_version}: {existing.id}"
            ),
        )

    request_source = "admin_api"
    sync_target = _structural_sync_target(kind)
    sync_job = (
        SyncJobRepository(session).get(version.id, sync_target)
        if sync_target is not None
        else None
    )
    lifecycle = {}
    if sync_job is not None:
        lifecycle = {
            "sync_job_id": str(sync_job.id),
            "sync_job_status_at_queue": str(sync_job.status),
            "sync_job_attempt_count_at_queue": sync_job.attempt_count,
        }
        if sync_job.status == SyncStatus.FAILED:
            request_source = "admin_api_retry"

    payload = {
        "active_only": True,
        "knowledge_version_id": str(version.id),
        "knowledge_version": version.knowledge_version,
        "erp_id": version.erp_id,
        **lifecycle,
        **parameters,
    }
    job = PipelineJobService(session).create(
        kind=kind,
        scope=PipelineJobScope.VERSION,
        target=version.knowledge_version,
        profile_name=request.app.state.pipeline_crawl_profile_name,
        erp_id=version.erp_id,
        knowledge_version_id=version.id,
        request_source=request_source,
        parameters=payload,
    )
    session.commit()
    request.app.state.pipeline_job_dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.post(
    "/crawl",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_crawl_job(
    payload: CrawlPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    dispatcher = request.app.state.pipeline_job_dispatcher
    parameters = {
        "headless": payload.headless,
        "slow_mo": payload.slow_mo,
    }
    target = payload.target
    erp_id = None
    knowledge_version_id = None

    if payload.scope == PipelineJobScope.MODULE:
        try:
            subtree = ModuleSubtreeResolver(session).resolve(
                payload.target_module_id or "",
                knowledge_version_id=payload.knowledge_version_id,
            )
        except ModuleSubtreeResolutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        target = subtree.root_module_id
        erp_id = subtree.erp_id
        knowledge_version_id = subtree.knowledge_version_id
        parameters.update(
            {
                "active_only": True,
                "target_module_id": subtree.root_module_id,
                "knowledge_version_id": str(subtree.knowledge_version_id),
                "knowledge_version": subtree.knowledge_version,
                "erp_id": subtree.erp_id,
            }
        )
    elif payload.scope == PipelineJobScope.SCREEN:
        try:
            screen = ScreenScopeResolver(session).resolve(
                payload.target or "",
                knowledge_version_id=payload.knowledge_version_id,
            )
        except ScreenScopeResolutionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        target = screen.route
        erp_id = screen.erp_id
        knowledge_version_id = screen.knowledge_version_id
        parameters.update(
            {
                "active_only": True,
                "target_screen_id": screen.screen_id,
                "knowledge_version_id": str(screen.knowledge_version_id),
                "knowledge_version": screen.knowledge_version,
                "erp_id": screen.erp_id,
            }
        )

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CRAWL,
        scope=payload.scope,
        target=target,
        profile_name=request.app.state.pipeline_crawl_profile_name,
        erp_id=erp_id,
        knowledge_version_id=knowledge_version_id,
        request_source="admin_api",
        parameters=parameters,
    )
    # El worker usa una sesión distinta; el job debe existir de forma visible
    # antes de colocarlo en la cola local.
    session.commit()
    dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.post(
    "/canonical-build",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_canonical_build_job(
    payload: CanonicalBuildPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    source = PipelineJobRepository(session).get(payload.source_crawl_job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="PipelineJob fuente no encontrado.")
    if source.kind != PipelineJobKind.CRAWL:
        raise HTTPException(
            status_code=409,
            detail="El job fuente debe ser de tipo crawl.",
        )
    if source.status != PipelineJobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail="El crawl fuente debe haber finalizado correctamente.",
        )
    result = dict(source.result_payload or {})
    if not result.get("artifact_root"):
        raise HTTPException(
            status_code=409,
            detail="El crawl fuente no registró artefactos utilizables.",
        )

    parameters = {"source_crawl_job_id": str(source.id)}
    if source.scope in {PipelineJobScope.MODULE, PipelineJobScope.SCREEN}:
        source_parameters = dict(source.parameters or {})
        required_context = {
            "base_knowledge_version_id": (
                str(source.knowledge_version_id) if source.knowledge_version_id else None
            ),
            "base_knowledge_version": source_parameters.get("knowledge_version"),
            "erp_id": source.erp_id or source_parameters.get("erp_id"),
        }
        if source.scope == PipelineJobScope.MODULE:
            required_context["target_module_id"] = source_parameters.get("target_module_id")
        else:
            required_context["target_screen_id"] = source_parameters.get("target_screen_id")
        if any(not value for value in required_context.values()):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El crawl {source.scope.value.upper()} fuente no conserva "
                    "su versión base fijada."
                ),
            )
        parameters.update(required_context)

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CANONICAL_BUILD,
        scope=source.scope,
        target=source.target,
        profile_name=source.profile_name,
        erp_id=source.erp_id,
        knowledge_version_id=source.knowledge_version_id,
        request_source="admin_api",
        parameters=parameters,
    )
    session.commit()
    request.app.state.pipeline_job_dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.post(
    "/canonical-merge",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_canonical_merge_job(
    payload: CanonicalMergePipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    source = PipelineJobRepository(session).get(payload.source_canonical_job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="PipelineJob fuente no encontrado.")
    if source.kind != PipelineJobKind.CANONICAL_BUILD:
        raise HTTPException(status_code=409, detail="El job fuente debe ser canonical_build.")
    if source.status != PipelineJobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail="El canonical build fuente debe haber finalizado correctamente.",
        )
    if source.scope not in {PipelineJobScope.MODULE, PipelineJobScope.SCREEN}:
        raise HTTPException(
            status_code=409,
            detail="canonical_merge sólo admite canonical builds MODULE o SCREEN.",
        )

    result = dict(source.result_payload or {})
    required = [
        "source_crawl_job_id",
        "knowledge_path",
        "manifest_path",
        "build_report_path",
        "knowledge_version",
        "base_knowledge_version_id",
        "base_knowledge_version",
    ]
    target_key = (
        "target_module_id"
        if source.scope == PipelineJobScope.MODULE
        else "target_screen_id"
    )
    required.append(target_key)
    if any(not result.get(key) for key in required):
        raise HTTPException(
            status_code=409,
            detail=(
                f"El canonical {source.scope.value.upper()} fuente no conserva "
                "artefactos/provenance fusionables."
            ),
        )
    if (
        result.get("snapshot_mode") != "partial"
        or result.get("snapshot_scope") != source.scope.value
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "canonical_merge requiere un snapshot parcial consistente con "
                f"scope={source.scope.value}."
            ),
        )

    try:
        base_version_id = uuid.UUID(str(result["base_knowledge_version_id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="El canonical parcial no conserva knowledge_version_id base válida.",
        ) from exc

    base = session.get(KnowledgeVersionRecord, base_version_id)
    erp_id = str(result.get("erp_id") or source.erp_id or "").strip()
    if (
        base is None
        or base.status != KnowledgeVersionStatus.ACTIVE
        or base.knowledge_version != str(result["base_knowledge_version"])
        or base.erp_id != erp_id
    ):
        raise HTTPException(
            status_code=409,
            detail="La versión base fijada por el canonical parcial ya no es ACTIVE.",
        )
    if source.knowledge_version_id and source.knowledge_version_id != base.id:
        raise HTTPException(
            status_code=409,
            detail="El canonical build fuente conserva una versión base inconsistente.",
        )

    target_value = str(result[target_key]).strip()
    expected_target = (
        target_value
        if source.scope == PipelineJobScope.MODULE
        else str(result.get("snapshot_target") or source.target or "").strip()
    )
    if expected_target != str(source.target or "").strip():
        raise HTTPException(
            status_code=409,
            detail="El canonical build fuente conserva un target inconsistente.",
        )

    parameters = {
        "source_canonical_job_id": str(source.id),
        "source_crawl_job_id": str(result["source_crawl_job_id"]),
        "knowledge_path": str(result["knowledge_path"]),
        "manifest_path": str(result["manifest_path"]),
        "build_report_path": str(result["build_report_path"]),
        "expected_partial_knowledge_version": str(result["knowledge_version"]),
        "base_knowledge_version_id": str(base.id),
        "base_knowledge_version": base.knowledge_version,
        "erp_id": base.erp_id,
        "active_only": True,
        target_key: target_value,
    }
    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CANONICAL_MERGE,
        scope=source.scope,
        target=str(source.target),
        profile_name=source.profile_name,
        erp_id=base.erp_id,
        knowledge_version_id=base.id,
        request_source="admin_api",
        parameters=parameters,
    )
    session.commit()
    request.app.state.pipeline_job_dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.post(
    "/canonical-reconciliation",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_canonical_reconciliation_job(
    payload: CanonicalReconciliationPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    try:
        review = RemovalReconciliationReviewService(session).get(payload.candidate_version_id)
    except RemovalReconciliationReviewNotPreparedError as exc:
        raise HTTPException(
            status_code=409,
            detail="El RAW candidate requiere preparar Removal HITL antes de reconciliar.",
        ) from exc
    except (RemovalReconciliationReviewError, LookupError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if review.pending_review != 0:
        raise HTTPException(
            status_code=409,
            detail=(
                "Canonical reconciliation requiere resolver todas las decisiones de Removal HITL."
            ),
        )
    if review.candidate_origin not in {
        "full_canonical",
        "partial_module_merge",
        "partial_screen_merge",
    }:
        raise HTTPException(
            status_code=409,
            detail="Removal HITL no corresponde a un RAW candidate reconciliable.",
        )

    try:
        active_id = uuid.UUID(review.active_version_id)
        candidate_id = uuid.UUID(review.candidate_version_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Removal HITL conserva identificadores de versión inválidos.",
        ) from exc

    candidate = session.get(KnowledgeVersionRecord, candidate_id)
    active = session.get(KnowledgeVersionRecord, active_id)
    if (
        candidate is None
        or candidate.status != KnowledgeVersionStatus.IMPORTED
        or candidate.knowledge_version != review.candidate_knowledge_version
        or candidate.erp_id != review.erp_id
    ):
        raise HTTPException(
            status_code=409,
            detail="El RAW candidate de Removal HITL ya no está IMPORTED o cambió.",
        )
    if (
        active is None
        or active.status != KnowledgeVersionStatus.ACTIVE
        or active.knowledge_version != review.active_knowledge_version
        or active.erp_id != review.erp_id
    ):
        raise HTTPException(
            status_code=409,
            detail="La versión ACTIVE fijada por Removal HITL ya no es válida.",
        )

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CANONICAL_RECONCILIATION,
        scope=PipelineJobScope.VERSION,
        target=None,
        profile_name=request.app.state.pipeline_crawl_profile_name,
        erp_id=review.erp_id,
        knowledge_version_id=candidate.id,
        request_source="admin_api",
        parameters={
            "candidate_version_id": str(candidate.id),
            "candidate_knowledge_version": candidate.knowledge_version,
            "active_version_id": str(active.id),
            "active_knowledge_version": active.knowledge_version,
            "erp_id": review.erp_id,
        },
    )
    session.commit()
    request.app.state.pipeline_job_dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.post(
    "/canonical-import",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_canonical_import_job(
    payload: CanonicalImportPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    if payload.source_reconciliation_job_id is not None:
        source = PipelineJobRepository(session).get(payload.source_reconciliation_job_id)
        if source is None:
            raise HTTPException(status_code=404, detail="PipelineJob fuente no encontrado.")
        if source.kind != PipelineJobKind.CANONICAL_RECONCILIATION:
            raise HTTPException(
                status_code=409,
                detail="El job fuente debe ser canonical_reconciliation.",
            )
        if source.status != PipelineJobStatus.SUCCEEDED:
            raise HTTPException(
                status_code=409,
                detail="El canonical reconciliation fuente debe haber finalizado correctamente.",
            )
        result = dict(source.result_payload or {})
        required = (
            "erp_id",
            "raw_candidate_version_id",
            "base_active_version_id",
            "reconciled_knowledge_version",
            "decision_set_hash",
            "unresolved_total",
            "decisions",
        )
        if any(key not in result or result[key] is None for key in required):
            raise HTTPException(
                status_code=409,
                detail="El canonical_reconciliation fuente no conserva provenance importable.",
            )
        if result["unresolved_total"] != 0:
            raise HTTPException(
                status_code=409,
                detail="El canonical_reconciliation mantiene removals sin resolver.",
            )
        decisions = result["decisions"]
        if not isinstance(decisions, list) or any(
            not isinstance(value, dict)
            or value.get("requires_human_review") is not False
            or not value.get("review_set_id")
            or not value.get("review_decision_id")
            or not value.get("review_action_id")
            or not isinstance(value.get("review_revision"), int)
            or value["review_revision"] <= 0
            for value in decisions
        ):
            raise HTTPException(
                status_code=409,
                detail="El canonical_reconciliation no conserva Removal HITL resuelto.",
            )
        try:
            raw_id = uuid.UUID(str(result["raw_candidate_version_id"]))
            active_id = uuid.UUID(str(result["base_active_version_id"]))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="El canonical_reconciliation conserva pins de versión inválidos.",
            ) from exc
        raw = session.get(KnowledgeVersionRecord, raw_id)
        active = session.get(KnowledgeVersionRecord, active_id)
        erp_id = str(result["erp_id"])
        if (
            raw is None
            or raw.status != KnowledgeVersionStatus.IMPORTED
            or raw.erp_id != erp_id
            or active is None
            or active.status != KnowledgeVersionStatus.ACTIVE
            or active.erp_id != erp_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Los pins RAW/ACTIVE del canonical_reconciliation ya no son válidos.",
            )
        job = PipelineJobService(session).create(
            kind=PipelineJobKind.CANONICAL_IMPORT,
            scope=PipelineJobScope.VERSION,
            target=None,
            profile_name=source.profile_name,
            erp_id=erp_id,
            knowledge_version_id=raw.id,
            request_source="admin_api",
            parameters={
                "source_reconciliation_job_id": str(source.id),
                "activation_mode": "staging_only",
                "erp_id": erp_id,
                "raw_candidate_version_id": str(raw.id),
                "base_active_version_id": str(active.id),
                "expected_decision_set_hash": str(result["decision_set_hash"]),
                "expected_knowledge_version": str(result["reconciled_knowledge_version"]),
            },
        )
        session.commit()
        request.app.state.pipeline_job_dispatcher.submit(job.id)
        return pipeline_job_detail(job)

    source = PipelineJobRepository(session).get(payload.source_canonical_job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="PipelineJob fuente no encontrado.")
    if source.kind not in {
        PipelineJobKind.CANONICAL_BUILD,
        PipelineJobKind.CANONICAL_MERGE,
    }:
        raise HTTPException(
            status_code=409,
            detail="El job fuente debe ser canonical_build o canonical_merge.",
        )
    if source.status != PipelineJobStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail="El canonical build fuente debe haber finalizado correctamente.",
        )
    result = dict(source.result_payload or {})
    required = (
        "source_crawl_job_id",
        "knowledge_path",
        "manifest_path",
        "build_report_path",
        "knowledge_version",
    )
    if any(not result.get(key) for key in required):
        raise HTTPException(
            status_code=409,
            detail="El canonical build fuente no registró artefactos importables.",
        )
    if result.get("snapshot_mode") != "full":
        raise HTTPException(
            status_code=409,
            detail=(
                "Un canonical parcial no se importa directamente; "
                "debe fusionarse con su versión base ACTIVE."
            ),
        )

    import_parameters = {
        "source_canonical_job_id": str(source.id),
        "source_crawl_job_id": str(result["source_crawl_job_id"]),
        "knowledge_path": str(result["knowledge_path"]),
        "manifest_path": str(result["manifest_path"]),
        "build_report_path": str(result["build_report_path"]),
        "expected_knowledge_version": str(result["knowledge_version"]),
        "activation_mode": "staging_only",
    }
    pinned_erp_id = None
    pinned_base_id = None
    if source.kind == PipelineJobKind.CANONICAL_MERGE:
        required_merge = (
            "base_knowledge_version_id",
            "base_knowledge_version",
            "erp_id",
            "merged_from_scope",
        )
        if any(not result.get(key) for key in required_merge):
            raise HTTPException(
                status_code=409,
                detail="El canonical_merge fuente no conserva su versión base fijada.",
            )
        merged_scope = str(result["merged_from_scope"]).strip()
        if merged_scope not in {"module", "screen"}:
            raise HTTPException(
                status_code=409,
                detail="El canonical_merge conserva un scope parcial inválido.",
            )
        target_key = (
            "target_module_id" if merged_scope == "module" else "target_screen_id"
        )
        if not result.get(target_key):
            raise HTTPException(
                status_code=409,
                detail="El canonical_merge no conserva el target parcial fijado.",
            )
        try:
            pinned_base_id = uuid.UUID(str(result["base_knowledge_version_id"]))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="El canonical_merge fuente conserva base_knowledge_version_id inválido.",
            ) from exc
        base = session.get(KnowledgeVersionRecord, pinned_base_id)
        pinned_erp_id = str(result["erp_id"]).strip()
        if (
            base is None
            or base.status != KnowledgeVersionStatus.ACTIVE
            or base.knowledge_version != str(result["base_knowledge_version"])
            or base.erp_id != pinned_erp_id
        ):
            raise HTTPException(
                status_code=409,
                detail="La versión base del canonical_merge ya no está ACTIVE.",
            )
        import_parameters.update(
            {
                "requires_active_base": True,
                "base_knowledge_version_id": str(base.id),
                "base_knowledge_version": base.knowledge_version,
                "erp_id": base.erp_id,
                "merged_from_scope": merged_scope,
                f"merged_{target_key}": str(result[target_key]),
            }
        )

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CANONICAL_IMPORT,
        scope=PipelineJobScope.FULL,
        target=None,
        profile_name=source.profile_name,
        erp_id=pinned_erp_id,
        knowledge_version_id=pinned_base_id,
        request_source="admin_api",
        parameters=import_parameters,
    )
    session.commit()
    request.app.state.pipeline_job_dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.post(
    "/neo4j-sync",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_neo4j_sync_job(
    payload: Neo4jSyncPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    return _queue_active_projection_job(
        request=request,
        session=session,
        kind=PipelineJobKind.NEO4J_SYNC,
        parameters={
            "batch_size": payload.batch_size,
            "replace_version": payload.replace_version,
        },
    )


@router.post(
    "/chroma-sync",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_chroma_sync_job(
    payload: ChromaSyncPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    return _queue_active_projection_job(
        request=request,
        session=session,
        kind=PipelineJobKind.CHROMA_SYNC,
        parameters={},
    )


@router.post(
    "/semantic-sync",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_semantic_sync_job(
    payload: SemanticSyncPipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    return _queue_active_projection_job(
        request=request,
        session=session,
        kind=PipelineJobKind.SEMANTIC_SYNC,
        parameters={"projection": "semantic_chromadb"},
    )


@router.post(
    "/semantic-inference",
    response_model=PipelineJobDetail,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_semantic_inference_job(
    payload: SemanticInferencePipelineJobCreateRequest,
    request: Request,
    session: WriteSessionDependency,
) -> PipelineJobDetail:
    version = _single_active_version(session)
    screen = KnowledgeRepository(session).get_item_by_identity(
        version.id, "screen", payload.screen_id
    )
    if screen is None:
        raise HTTPException(
            status_code=404,
            detail="La pantalla no existe en la versión ACTIVE.",
        )
    if screen.current_review_status not in {
        ReviewStatus.APPROVED,
        ReviewStatus.CORRECTED,
    }:
        raise HTTPException(
            status_code=409,
            detail="La pantalla requiere revisión estructural aprobada/corregida.",
        )

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.SEMANTIC_INFERENCE,
        scope=PipelineJobScope.SCREEN,
        target=screen.route or screen.canonical_id,
        profile_name=request.app.state.pipeline_crawl_profile_name,
        erp_id=version.erp_id,
        knowledge_version_id=version.id,
        request_source="admin_api",
        parameters={
            "active_only": True,
            "semantic_type": "screen_purpose",
            "knowledge_version_id": str(version.id),
            "knowledge_version": version.knowledge_version,
            "erp_id": version.erp_id,
            "screen_knowledge_item_id": str(screen.id),
            "screen_id": screen.canonical_id,
            "screen_route": screen.route,
        },
    )
    session.commit()
    request.app.state.pipeline_job_dispatcher.submit(job.id)
    return pipeline_job_detail(job)


@router.get("", response_model=PipelineJobListResponse)
def list_pipeline_jobs(
    session: SessionDependency,
    kind: PipelineJobKind | None = None,
    status: PipelineJobStatus | None = None,
    scope: PipelineJobScope | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PipelineJobListResponse:
    rows, total = PipelineJobRepository(session).list_page(
        kind=kind, status=status, scope=scope, limit=limit, offset=offset
    )
    return PipelineJobListResponse(
        items=tuple(pipeline_job_summary(job) for job in rows),
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + len(rows) if offset + len(rows) < total else None,
    )


@router.get("/{job_id}", response_model=PipelineJobDetail)
def get_pipeline_job(job_id: uuid.UUID, session: SessionDependency) -> PipelineJobDetail:
    job = PipelineJobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="PipelineJob no encontrado.")
    return pipeline_job_detail(job)
