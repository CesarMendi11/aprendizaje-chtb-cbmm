from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from src.database.enums import PipelineJobKind, PipelineJobScope, PipelineJobStatus
from src.database.repositories import PipelineJobRepository
from src.database.services import (
    ModuleSubtreeResolutionError,
    ModuleSubtreeResolver,
    PipelineJobService,
)
from src.pipeline.canonical_build_job_executor import CanonicalBuildJobExecutor
from src.pipeline.canonical_import_job_executor import CanonicalImportJobExecutor
from src.pipeline.chroma_sync_job_executor import ChromaSyncJobExecutor
from src.pipeline.crawl_job_executor import CrawlJobExecutor
from src.pipeline.neo4j_sync_job_executor import Neo4jSyncJobExecutor
from src.pipeline.semantic_chroma_sync_job_executor import SemanticChromaSyncJobExecutor
from src.pipeline.semantic_inference_job_executor import SemanticInferenceJobExecutor


@dataclass(frozen=True)
class PipelineJobSpec:
    id: uuid.UUID
    kind: PipelineJobKind
    scope: PipelineJobScope
    target: str | None
    erp_id: str | None
    knowledge_version_id: uuid.UUID | None
    parameters: dict[str, Any]


class PipelineJobRunner:
    """Consume queued jobs and persist progress in short independent transactions."""

    def __init__(
        self,
        session_factory,
        *,
        crawl_executor: CrawlJobExecutor | None = None,
        canonical_build_executor: CanonicalBuildJobExecutor | None = None,
        canonical_import_executor: CanonicalImportJobExecutor | None = None,
        neo4j_sync_executor: Neo4jSyncJobExecutor | None = None,
        chroma_sync_executor: ChromaSyncJobExecutor | None = None,
        semantic_inference_executor: SemanticInferenceJobExecutor | None = None,
        semantic_sync_executor: SemanticChromaSyncJobExecutor | None = None,
    ):
        self.session_factory = session_factory
        self.executors = {
            PipelineJobKind.CRAWL: crawl_executor or CrawlJobExecutor(),
            PipelineJobKind.CANONICAL_BUILD: canonical_build_executor
            or CanonicalBuildJobExecutor(),
            PipelineJobKind.CANONICAL_IMPORT: canonical_import_executor
            or CanonicalImportJobExecutor(session_factory),
            PipelineJobKind.NEO4J_SYNC: neo4j_sync_executor
            or Neo4jSyncJobExecutor(session_factory),
            PipelineJobKind.CHROMA_SYNC: chroma_sync_executor
            or ChromaSyncJobExecutor(session_factory),
            PipelineJobKind.SEMANTIC_INFERENCE: semantic_inference_executor
            or SemanticInferenceJobExecutor(session_factory),
            PipelineJobKind.SEMANTIC_SYNC: semantic_sync_executor
            or SemanticChromaSyncJobExecutor(session_factory),
        }

    def run(self, job_id: uuid.UUID | str) -> None:
        spec = self._start(job_id)
        if spec is None:
            return

        executor = self.executors.get(spec.kind)
        if executor is None:
            self._fail(spec.id, RuntimeError(f"Job kind no soportado por el runner: {spec.kind}"))
            return

        try:
            execution_parameters = self._execution_parameters(spec)
            result = executor.execute(
                job_id=spec.id,
                scope=spec.scope,
                target=spec.target,
                parameters=execution_parameters,
                progress=lambda stage, payload: self._checkpoint(spec.id, stage, payload),
            )
        except Exception as exc:
            self._fail(spec.id, exc)
            return

        with self.session_factory.begin() as session:
            PipelineJobService(session).succeed(
                spec.id,
                result_payload=result,
                stage="completed",
                erp_id=result.get("erp_id"),
                knowledge_version_id=result.get("knowledge_version_id"),
            )

    def _start(self, job_id: uuid.UUID | str) -> PipelineJobSpec | None:
        with self.session_factory.begin() as session:
            job = PipelineJobRepository(session).get(job_id, for_update=True)
            if job is None or job.status != PipelineJobStatus.QUEUED:
                return None
            PipelineJobService(session).start(job.id, stage="starting")
            return PipelineJobSpec(
                id=job.id,
                kind=job.kind,
                scope=job.scope,
                target=job.target,
                erp_id=job.erp_id,
                knowledge_version_id=job.knowledge_version_id,
                parameters=dict(job.parameters or {}),
            )

    def _execution_parameters(self, spec: PipelineJobSpec) -> dict[str, Any]:
        parameters = dict(spec.parameters)
        if spec.kind != PipelineJobKind.CRAWL or spec.scope != PipelineJobScope.MODULE:
            return parameters

        target_module_id = str(
            parameters.get("target_module_id") or spec.target or ""
        ).strip()
        if not target_module_id or target_module_id != str(spec.target or "").strip():
            raise RuntimeError(
                "El job MODULE no conserva un target_module_id consistente"
            )

        pinned_version_id = spec.knowledge_version_id or parameters.get(
            "knowledge_version_id"
        )
        if pinned_version_id is None:
            raise RuntimeError(
                "El job MODULE no conserva knowledge_version_id fijado"
            )

        parameter_version_id = str(parameters.get("knowledge_version_id") or "").strip()
        if parameter_version_id and parameter_version_id != str(pinned_version_id):
            raise RuntimeError(
                "El job MODULE contiene knowledge_version_id inconsistente"
            )

        try:
            with self.session_factory() as session:
                subtree = ModuleSubtreeResolver(session).resolve(
                    target_module_id,
                    knowledge_version_id=pinned_version_id,
                )
        except ModuleSubtreeResolutionError as exc:
            raise RuntimeError(
                f"No fue posible validar el scope MODULE fijado: {exc}"
            ) from exc

        if spec.erp_id and spec.erp_id != subtree.erp_id:
            raise RuntimeError("El job MODULE pertenece a un ERP distinto del fijado")
        parameter_erp_id = str(parameters.get("erp_id") or "").strip()
        if parameter_erp_id and parameter_erp_id != subtree.erp_id:
            raise RuntimeError("El job MODULE contiene erp_id inconsistente")
        parameter_version = str(parameters.get("knowledge_version") or "").strip()
        if parameter_version and parameter_version != subtree.knowledge_version:
            raise RuntimeError(
                "El job MODULE contiene knowledge_version inconsistente"
            )

        parameters.update(
            {
                "target_module_id": subtree.root_module_id,
                "knowledge_version_id": str(subtree.knowledge_version_id),
                "knowledge_version": subtree.knowledge_version,
                "erp_id": subtree.erp_id,
                "module_scope": {
                    "root_module_id": subtree.root_module_id,
                    "root_module_name": subtree.root_module_name,
                    "ancestor_module_ids": list(subtree.ancestor_module_ids),
                    "module_ids": list(subtree.module_ids),
                    "known_screen_ids": list(subtree.known_screen_ids),
                    "known_screen_routes": list(subtree.known_screen_routes),
                    "unroutable_screen_ids": list(subtree.unroutable_screen_ids),
                    "navigation_path": list(subtree.navigation_path),
                    "navigation_origin_path": list(subtree.navigation_origin_path),
                },
            }
        )
        return parameters

    def _checkpoint(self, job_id: uuid.UUID, stage: str, payload: dict[str, Any]) -> None:
        current = int(payload.get("work_units", 0) or 0)
        total_value = payload.get("progress_total")
        total = int(total_value) if total_value is not None else None
        checkpoint = {
            key: value
            for key, value in payload.items()
            if key not in {"work_units", "progress_total"}
        }
        with self.session_factory.begin() as session:
            job = PipelineJobRepository(session).get(job_id)
            if job is None or job.status != PipelineJobStatus.RUNNING:
                return
            PipelineJobService(session).checkpoint(
                job_id,
                stage=stage,
                progress_current=max(0, current),
                progress_total=total,
                checkpoint=checkpoint,
            )

    def _fail(self, job_id: uuid.UUID, error: Exception) -> None:
        try:
            with self.session_factory.begin() as session:
                job = PipelineJobRepository(session).get(job_id)
                if job is None or job.status != PipelineJobStatus.RUNNING:
                    return
                PipelineJobService(session).fail(job_id, error)
        except Exception:
            # El executor puede haber producido artefactos aislados si alcanzó a iniciar.
            # No se expone un error secundario del worker al hilo de la API.
            return
