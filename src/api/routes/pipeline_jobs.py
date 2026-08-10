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
from src.database.services import PipelineJobService

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
            detail="Se requiere exactamente una versión ACTIVE para sincronizar.",
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
    job = PipelineJobService(session).create(
        kind=PipelineJobKind.CRAWL,
        scope=payload.scope,
        target=payload.target,
        profile_name=request.app.state.pipeline_crawl_profile_name,
        request_source="admin_api",
        parameters={
            "headless": payload.headless,
            "slow_mo": payload.slow_mo,
        },
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
