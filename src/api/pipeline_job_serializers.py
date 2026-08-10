from __future__ import annotations

from src.api.schemas.pipeline_jobs import PipelineJobDetail, PipelineJobSummary
from src.database.enums import PipelineJobStatus
from src.database.models import PipelineJob


def _progress(job: PipelineJob) -> float | None:
    if job.progress_total is None:
        return None
    if job.progress_total == 0:
        return 100.0 if job.status == PipelineJobStatus.SUCCEEDED else 0.0
    return round(min(100.0, (job.progress_current / job.progress_total) * 100.0), 2)


def pipeline_job_summary(job: PipelineJob) -> PipelineJobSummary:
    return PipelineJobSummary(
        id=job.id,
        kind=job.kind,
        status=job.status,
        scope=job.scope,
        target=job.target,
        profile_name=job.profile_name,
        erp_id=job.erp_id,
        knowledge_version_id=job.knowledge_version_id,
        request_source=job.request_source,
        stage=job.stage,
        progress_current=job.progress_current,
        progress_total=job.progress_total,
        progress_percent=_progress(job),
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_summary=job.error_summary,
    )


def pipeline_job_detail(job: PipelineJob) -> PipelineJobDetail:
    return PipelineJobDetail(
        **pipeline_job_summary(job).model_dump(),
        parameters=dict(job.parameters or {}),
        checkpoint=dict(job.checkpoint or {}),
        result_payload=dict(job.result_payload) if job.result_payload is not None else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )
