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
    ChromaSyncPipelineJobCreateRequest,
    CrawlPipelineJobCreateRequest,
    Neo4jSyncPipelineJobCreateRequest,
    SemanticInferencePipelineJobCreateRequest,
    SemanticSyncPipelineJobCreateRequest,
    PipelineJobDetail,
    PipelineJobListResponse,
)
from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)
from src.database.repositories import KnowledgeRepository, PipelineJobRepository
from src.database.services import (
    ModuleSubtreeResolutionError,
    ModuleSubtreeResolver,
    PipelineJobService,
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

    payload = {
        "active_only": True,
        "knowledge_version_id": str(version.id),
        "knowledge_version": version.knowledge_version,
        "erp_id": version.erp_id,
        **parameters,
    }
    job = PipelineJobService(session).create(
        kind=kind,
        scope=PipelineJobScope.VERSION,
        target=version.knowledge_version,
        profile_name=request.app.state.pipeline_crawl_profile_name,
        erp_id=version.erp_id,
        knowledge_version_id=version.id,
        request_source="admin_api",
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
                payload.target_module_id or ""
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

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CANONICAL_BUILD,
        scope=source.scope,
        target=source.target,
        profile_name=source.profile_name,
        request_source="admin_api",
        parameters={"source_crawl_job_id": str(source.id)},
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
    source = PipelineJobRepository(session).get(payload.source_canonical_job_id)
    if source is None:
        raise HTTPException(status_code=404, detail="PipelineJob fuente no encontrado.")
    if source.kind != PipelineJobKind.CANONICAL_BUILD:
        raise HTTPException(
            status_code=409,
            detail="El job fuente debe ser de tipo canonical_build.",
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

    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CANONICAL_IMPORT,
        scope=source.scope,
        target=source.target,
        profile_name=source.profile_name,
        request_source="admin_api",
        parameters={
            "source_canonical_job_id": str(source.id),
            "source_crawl_job_id": str(result["source_crawl_job_id"]),
            "knowledge_path": str(result["knowledge_path"]),
            "manifest_path": str(result["manifest_path"]),
            "build_report_path": str(result["build_report_path"]),
            "expected_knowledge_version": str(result["knowledge_version"]),
            "activation_mode": "staging_only",
        },
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
