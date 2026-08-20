from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
    SyncStatus,
    SyncTarget,
)
from src.database.models import (
    KnowledgeItem,
    KnowledgeVersionPromotion,
    KnowledgeVersionRecord,
    PipelineJob,
    SyncJob,
)
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import is_safe_navigation_metadata
from src.knowledge.crawl_execution_quality import (
    CrawlExecutionQualityError,
    validate_certified_quality_source,
    validate_matching_certified_quality,
)

from .canonical_materialization_service import (
    CanonicalKnowledgeMaterializationError,
    CanonicalKnowledgeMaterializer,
)
from .version_diff_service import (
    VersionDiff,
    VersionDiffCandidateOrigin,
    VersionDiffChangeType,
    VersionDiffError,
    VersionDiffService,
)

PUBLISHABLE_REVIEW_STATUSES = {
    ReviewStatus.APPROVED,
    ReviewStatus.CORRECTED,
}
BOOTSTRAP_REQUIRED_ENTITY_TYPES = ("erp_system", "module")


class KnowledgePromotionError(ValueError):
    pass


class KnowledgePromotionBlockedError(KnowledgePromotionError):
    def __init__(self, assessment: "PromotionAssessment"):
        super().__init__("La KnowledgeVersion no supera el Promotion Gate")
        self.assessment = assessment


@dataclass(frozen=True)
class PromotionBlocker:
    code: str
    message: str
    count: int = 1
    entity_type: str | None = None


@dataclass(frozen=True)
class PromotionAssessment:
    knowledge_version_id: str
    knowledge_version: str
    erp_id: str
    version_status: str
    promotable: bool
    bootstrap_promotion: bool
    promotion_mode: str
    current_active_version_id: str | None
    current_active_knowledge_version: str | None
    required_entity_types: tuple[str, ...]
    required_review_counts: dict[str, dict[str, int]]
    all_review_counts: dict[str, int]
    replacement_review_counts: dict[str, int]
    diff_totals: dict[str, int] | None
    pipeline_import_job_id: str | None
    source_canonical_job_id: str | None
    source_reconciliation_job_id: str | None
    removal_review_set_id: str | None
    decision_set_hash: str | None
    build_warning_count: int
    blockers: tuple[PromotionBlocker, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class PromotionResult:
    promotion_id: str
    knowledge_version_id: str
    knowledge_version: str
    erp_id: str
    previous_active_version_id: str | None
    sync_jobs: dict[str, str]
    assessment: PromotionAssessment


class KnowledgePromotionService:
    """Fail-closed bootstrap and replacement Promotion Gate."""

    def __init__(self, session: Session):
        self.session = session

    def assess(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        for_update: bool = False,
    ) -> PromotionAssessment:
        version_id = self._uuid(knowledge_version_id)
        version = self._version(version_id, for_update=for_update)
        if version is None:
            raise LookupError("KnowledgeVersion no encontrada")

        blockers: list[PromotionBlocker] = []
        warnings: list[str] = []

        if version.status != KnowledgeVersionStatus.IMPORTED:
            blockers.append(
                PromotionBlocker(
                    "version_not_imported",
                    "Solo una KnowledgeVersion IMPORTED puede promoverse.",
                )
            )

        active = self._active_version(version.erp_id, for_update=for_update)
        bootstrap = active is None
        promotion_mode = "bootstrap" if bootstrap else "replacement"

        import_job: PipelineJob | None = None
        source_job: PipelineJob | None = None
        source_reconciliation_job: PipelineJob | None = None
        removal_review_set_id: str | None = None
        decision_set_hash: str | None = None
        diff: VersionDiff | None = None
        replacement_review_counts: dict[str, int] = {}

        if bootstrap:
            import_job, source_job = self._bootstrap_provenance(version, blockers)
            required_counts, all_counts, review_blockers = self._review_state(version.id)
            blockers.extend(review_blockers)
            required_entity_types = BOOTSTRAP_REQUIRED_ENTITY_TYPES
        else:
            (
                import_job,
                source_reconciliation_job,
                diff,
                removal_review_set_id,
                decision_set_hash,
            ) = self._replacement_provenance(version, active, blockers)
            required_counts = {}
            required_entity_types = ()
            all_counts = self._all_review_counts(version.id)
            if diff is not None:
                replacement_review_counts, review_blockers = self._replacement_review_state(diff)
                blockers.extend(review_blockers)

        try:
            CanonicalKnowledgeMaterializer(self.session).materialize(version.id)
        except CanonicalKnowledgeMaterializationError as exc:
            blockers.append(
                PromotionBlocker(
                    "canonical_materialization_invalid",
                    str(exc),
                )
            )

        blockers.extend(self._module_crawl_readiness(version.id))

        existing_sync_jobs = list(
            self.session.scalars(
                select(SyncJob).where(SyncJob.knowledge_version_id == version.id)
            )
        )
        if existing_sync_jobs:
            blockers.append(
                PromotionBlocker(
                    "preexisting_projection_jobs",
                    "Una versión STAGING no debe tener SyncJobs estructurales "
                    "antes de la promoción.",
                    count=len(existing_sync_jobs),
                )
            )

        build_warning_count = len(version.build_warnings or [])
        if build_warning_count:
            warnings.append(
                f"El build conserva {build_warning_count} warning(s); no son "
                "bloqueantes porque el canonical validó sin errores."
            )

        return PromotionAssessment(
            knowledge_version_id=str(version.id),
            knowledge_version=version.knowledge_version,
            erp_id=version.erp_id,
            version_status=str(version.status),
            promotable=not blockers,
            bootstrap_promotion=bootstrap,
            promotion_mode=promotion_mode,
            current_active_version_id=str(active.id) if active else None,
            current_active_knowledge_version=active.knowledge_version if active else None,
            required_entity_types=required_entity_types,
            required_review_counts=required_counts,
            all_review_counts=all_counts,
            replacement_review_counts=replacement_review_counts,
            diff_totals=diff.totals if diff is not None else None,
            pipeline_import_job_id=str(import_job.id) if import_job else None,
            source_canonical_job_id=str(source_job.id) if source_job else None,
            source_reconciliation_job_id=(
                str(source_reconciliation_job.id) if source_reconciliation_job else None
            ),
            removal_review_set_id=removal_review_set_id,
            decision_set_hash=decision_set_hash,
            build_warning_count=build_warning_count,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    def promote(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        reviewer: str,
        reason: str,
        expected_knowledge_version: str,
    ) -> PromotionResult:
        reviewer = " ".join(str(reviewer or "").split())
        reason = " ".join(str(reason or "").split())
        expected = str(expected_knowledge_version or "").strip()
        if not reviewer:
            raise KnowledgePromotionError("reviewer no puede estar vacío")
        if not reason:
            raise KnowledgePromotionError("La promoción requiere una justificación")
        if not expected:
            raise KnowledgePromotionError("expected_knowledge_version no puede estar vacío")

        assessment = self.assess(knowledge_version_id, for_update=True)
        if assessment.knowledge_version != expected:
            raise KnowledgePromotionError("La KnowledgeVersion cambió; recargue la evaluación")
        if not assessment.promotable:
            raise KnowledgePromotionBlockedError(assessment)

        version = self._version(self._uuid(knowledge_version_id), for_update=True)
        assert version is not None
        active = self._active_version(version.erp_id, for_update=True)

        if assessment.bootstrap_promotion:
            if active is not None:
                raise KnowledgePromotionBlockedError(self.assess(version.id, for_update=True))
            previous_active = None
        else:
            if (
                active is None
                or assessment.current_active_version_id is None
                or str(active.id) != assessment.current_active_version_id
            ):
                raise KnowledgePromotionBlockedError(self.assess(version.id, for_update=True))
            previous_active = active

        promotion = KnowledgeVersionPromotion(
            knowledge_version_id=version.id,
            previous_active_version_id=(previous_active.id if previous_active else None),
            reviewer_subject=reviewer[:240],
            reason=reason[:4000],
            source="api",
            gate_snapshot=self._assessment_payload(assessment),
        )
        self.session.add(promotion)

        if previous_active is not None:
            previous_active.status = KnowledgeVersionStatus.ARCHIVED
        version.status = KnowledgeVersionStatus.ACTIVE

        sync_jobs: dict[str, str] = {}
        for target in SyncTarget:
            job = SyncJob(
                knowledge_version_id=version.id,
                target=target,
                status=SyncStatus.PENDING,
            )
            self.session.add(job)
            self.session.flush()
            sync_jobs[target.value] = str(job.id)

        self.session.flush()
        return PromotionResult(
            promotion_id=str(promotion.id),
            knowledge_version_id=str(version.id),
            knowledge_version=version.knowledge_version,
            erp_id=version.erp_id,
            previous_active_version_id=(str(previous_active.id) if previous_active else None),
            sync_jobs=sync_jobs,
            assessment=assessment,
        )

    def promote_bootstrap(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        reviewer: str,
        reason: str,
        expected_knowledge_version: str,
    ) -> PromotionResult:
        assessment = self.assess(knowledge_version_id)
        if not assessment.bootstrap_promotion:
            raise KnowledgePromotionBlockedError(assessment)
        return self.promote(
            knowledge_version_id,
            reviewer=reviewer,
            reason=reason,
            expected_knowledge_version=expected_knowledge_version,
        )

    def promote_replacement(
        self,
        knowledge_version_id: uuid.UUID | str,
        *,
        reviewer: str,
        reason: str,
        expected_knowledge_version: str,
    ) -> PromotionResult:
        assessment = self.assess(knowledge_version_id)
        if assessment.bootstrap_promotion:
            raise KnowledgePromotionBlockedError(assessment)
        return self.promote(
            knowledge_version_id,
            reviewer=reviewer,
            reason=reason,
            expected_knowledge_version=expected_knowledge_version,
        )

    def _bootstrap_provenance(
        self,
        version: KnowledgeVersionRecord,
        blockers: list[PromotionBlocker],
    ) -> tuple[PipelineJob | None, PipelineJob | None]:
        import_job, source_job = self._pipeline_provenance(version)
        if import_job is None:
            blockers.append(
                PromotionBlocker(
                    "missing_pipeline_import_provenance",
                    "La versión no conserva un canonical_import exitoso y gobernado del pipeline.",
                )
            )
        else:
            parameters = dict(import_job.parameters or {})
            result = dict(import_job.result_payload or {})
            if import_job.scope != PipelineJobScope.FULL:
                blockers.append(
                    PromotionBlocker(
                        "import_scope_not_full",
                        "El canonical_import de bootstrap no tiene scope=FULL.",
                    )
                )
            if parameters.get("activation_mode") != "staging_only":
                blockers.append(
                    PromotionBlocker(
                        "import_not_staging_only",
                        "La versión no fue importada en staging_only.",
                    )
                )
            if result.get("staging_ready") is not True:
                blockers.append(
                    PromotionBlocker(
                        "staging_not_ready",
                        "El canonical_import no declaró staging_ready=true.",
                    )
                )
            if result.get("activation_performed") is not False:
                blockers.append(
                    PromotionBlocker(
                        "unexpected_activation_history",
                        "La procedencia indica activación previa o ambigua.",
                    )
                )
            if result.get("knowledge_version") != version.knowledge_version:
                blockers.append(
                    PromotionBlocker(
                        "import_version_mismatch",
                        "El import no coincide con la KnowledgeVersion objetivo.",
                    )
                )
            if str(result.get("knowledge_version_id") or "") != str(version.id):
                blockers.append(
                    PromotionBlocker(
                        "import_version_id_mismatch",
                        "El import no apunta a la KnowledgeVersion objetivo.",
                    )
                )
            if source_job is not None:
                self._append_crawl_quality_blocker(
                    import_job,
                    source_job,
                    blockers,
                )

        if source_job is None:
            blockers.append(
                PromotionBlocker(
                    "missing_source_canonical_provenance",
                    "No se pudo verificar el canonical_build/canonical_merge fuente.",
                )
            )
        else:
            source_result = dict(source_job.result_payload or {})
            if source_job.status != PipelineJobStatus.SUCCEEDED:
                blockers.append(
                    PromotionBlocker(
                        "source_canonical_not_succeeded",
                        "El canonical fuente no terminó correctamente.",
                    )
                )
            if (
                source_result.get("snapshot_mode") != "full"
                or source_result.get("snapshot_scope") != "full"
            ):
                blockers.append(
                    PromotionBlocker(
                        "source_snapshot_not_full",
                        "Promotion Gate requiere un snapshot canónico FULL.",
                    )
                )
            if source_result.get("knowledge_version") != version.knowledge_version:
                blockers.append(
                    PromotionBlocker(
                        "source_canonical_version_mismatch",
                        "El canonical fuente no coincide con la KnowledgeVersion importada.",
                    )
                )
        return import_job, source_job

    @staticmethod
    def _append_crawl_quality_blocker(
        import_job: PipelineJob,
        source_job: PipelineJob,
        blockers: list[PromotionBlocker],
    ) -> None:
        try:
            source_result = dict(source_job.result_payload or {})
            execution_quality = validate_matching_certified_quality(
                source_result.get("crawl_execution_quality"),
                dict(import_job.result_payload or {}).get("crawl_execution_quality"),
                dict(import_job.parameters or {}).get("expected_crawl_execution_quality"),
            )
            validate_certified_quality_source(
                execution_quality,
                source_run_id=source_result.get("source_crawl_job_id"),
                source_scope=source_job.scope.value,
                source_target=source_job.target,
                check_target=True,
            )
        except CrawlExecutionQualityError as exc:
            blockers.append(
                PromotionBlocker(
                    "crawl_quality_not_certified",
                    "La provenance del candidate no conserva calidad de crawl "
                    f"certificada: {exc}",
                )
            )

    def _replacement_provenance(
        self,
        version: KnowledgeVersionRecord,
        active: KnowledgeVersionRecord,
        blockers: list[PromotionBlocker],
    ) -> tuple[PipelineJob | None, PipelineJob | None, VersionDiff | None, str | None, str | None]:
        try:
            diff = VersionDiffService(self.session).compare(version.id)
        except (VersionDiffError, LookupError) as exc:
            blockers.append(PromotionBlocker("replacement_diff_invalid", str(exc)))
            return None, None, None, None, None

        if diff.candidate_origin != VersionDiffCandidateOrigin.RECONCILED_FULL.value:
            blockers.append(
                PromotionBlocker(
                    "replacement_not_reconciled",
                    "Una sustitución ACTIVE requiere un candidate FULL reconciliado.",
                )
            )

        import_job = self._origin_import(version)
        if import_job is None:
            blockers.append(
                PromotionBlocker(
                    "missing_pipeline_import_provenance",
                    "Falta canonical_import reconciliado.",
                )
            )
            return None, None, diff, None, None
        parameters = dict(import_job.parameters or {})
        try:
            source_id = uuid.UUID(str(parameters.get("source_reconciliation_job_id")))
        except (TypeError, ValueError):
            blockers.append(
                PromotionBlocker(
                    "missing_reconciliation_provenance",
                    "Falta source_reconciliation_job_id válido.",
                )
            )
            return import_job, None, diff, None, None
        source = self.session.get(PipelineJob, source_id)
        if source is None:
            blockers.append(
                PromotionBlocker(
                    "missing_reconciliation_provenance",
                    "No existe el source reconciliation job.",
                )
            )
            return import_job, None, diff, None, None

        source_result = dict(source.result_payload or {})
        decisions = list(source_result.get("decisions") or [])
        review_set_ids = {
            str(value.get("review_set_id"))
            for value in decisions
            if value.get("review_set_id")
        }
        review_set_id = next(iter(review_set_ids)) if len(review_set_ids) == 1 else None
        decision_set_hash = source_result.get("decision_set_hash")

        if (
            source_result.get("confirmed_removed_total")
            != diff.totals[VersionDiffChangeType.REMOVED.value]
        ):
            blockers.append(
                PromotionBlocker(
                    "replacement_removed_count_mismatch",
                    "Los REMOVED finales no coinciden con las eliminaciones confirmadas por HITL.",
                )
            )
        if decisions and review_set_id is None:
            blockers.append(
                PromotionBlocker(
                    "removal_hitl_ambiguous",
                    "Las decisiones de removal no pertenecen a un único ReviewSet.",
                )
            )
        if source_result.get("unresolved_total") != 0:
            blockers.append(
                PromotionBlocker(
                    "removal_hitl_unresolved",
                    "Existen decisiones de removal sin resolver.",
                )
            )
        if source_result.get("base_active_version_id") != str(active.id):
            blockers.append(
                PromotionBlocker(
                    "replacement_base_active_mismatch",
                    "La reconciliación no parte de la ACTIVE actual.",
                )
            )

        return import_job, source, diff, review_set_id, str(decision_set_hash or "") or None

    def _replacement_review_state(
        self,
        diff: VersionDiff,
    ) -> tuple[dict[str, int], list[PromotionBlocker]]:
        changed = [
            item
            for item in diff.items
            if item.change_type in {VersionDiffChangeType.NEW, VersionDiffChangeType.MODIFIED}
        ]
        counts = Counter(str(item.candidate_review_status) for item in changed)
        blockers: list[PromotionBlocker] = []
        non_publishable = [
            item
            for item in changed
            if item.candidate_review_status
            not in {status.value for status in PUBLISHABLE_REVIEW_STATUSES}
        ]
        if non_publishable:
            blockers.append(
                PromotionBlocker(
                    "replacement_structural_review_incomplete",
                    "Todos los elementos NEW/MODIFIED deben estar APPROVED o "
                    "CORRECTED antes del reemplazo.",
                    count=len(non_publishable),
                )
            )
        return dict(sorted(counts.items())), blockers

    def _review_state(
        self,
        version_id: uuid.UUID,
    ) -> tuple[dict[str, dict[str, int]], dict[str, int], list[PromotionBlocker]]:
        items = list(
            self.session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == version_id
                )
            )
        )
        all_counts = Counter(str(item.current_review_status) for item in items)
        required_counts: dict[str, dict[str, int]] = {}
        blockers: list[PromotionBlocker] = []

        for entity_type in BOOTSTRAP_REQUIRED_ENTITY_TYPES:
            selected = [item for item in items if item.entity_type == entity_type]
            counts = Counter(str(item.current_review_status) for item in selected)
            required_counts[entity_type] = dict(sorted(counts.items()))
            if entity_type == "erp_system" and len(selected) != 1:
                blockers.append(
                    PromotionBlocker(
                        "invalid_erp_system_count",
                        "La versión debe contener exactamente un erp_system.",
                        count=len(selected),
                        entity_type=entity_type,
                    )
                )
            for status in (ReviewStatus.PENDING_REVIEW, ReviewStatus.REJECTED):
                count = sum(1 for item in selected if item.current_review_status == status)
                if count:
                    blockers.append(
                        PromotionBlocker(
                            f"required_{status.value}",
                            f"Los elementos {entity_type} obligatorios no pueden "
                            f"permanecer en {status.value}.",
                            count=count,
                            entity_type=entity_type,
                        )
                    )
            non_publishable = [
                item
                for item in selected
                if item.current_review_status not in PUBLISHABLE_REVIEW_STATUSES
            ]
            known_blocked = sum(
                1
                for item in selected
                if item.current_review_status
                in {ReviewStatus.PENDING_REVIEW, ReviewStatus.REJECTED}
            )
            if len(non_publishable) > known_blocked:
                blockers.append(
                    PromotionBlocker(
                        "required_review_status_unsupported",
                        f"Hay elementos {entity_type} obligatorios con estado no publicable.",
                        count=len(non_publishable) - known_blocked,
                        entity_type=entity_type,
                    )
                )

        return required_counts, dict(sorted(all_counts.items())), blockers

    def _all_review_counts(self, version_id: uuid.UUID) -> dict[str, int]:
        items = list(
            self.session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == version_id
                )
            )
        )
        return dict(sorted(Counter(str(item.current_review_status) for item in items).items()))

    def _module_crawl_readiness(self, version_id: uuid.UUID) -> list[PromotionBlocker]:
        modules = list(
            self.session.scalars(
                select(KnowledgeItem).where(
                    KnowledgeItem.knowledge_version_id == version_id,
                    KnowledgeItem.entity_type == "module",
                )
            )
        )
        invalid = 0
        for item in modules:
            payload = dict(item.source_payload or {})
            navigation_path = payload.get("navigation_path")
            labels = (
                [str(value or "").strip() for value in navigation_path]
                if isinstance(navigation_path, (list, tuple))
                else []
            )
            labels = [value for value in labels if value]
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                invalid += 1
                continue
            origin_value = metadata.get("navigation_origin_path")
            if not is_safe_navigation_metadata("navigation_origin_path", origin_value):
                invalid += 1
                continue
            origins = [value.strip() for value in str(origin_value).split("||") if value.strip()]
            if not labels or len(labels) != len(origins):
                invalid += 1
        if not invalid:
            return []
        return [
            PromotionBlocker(
                "module_navigation_unreproducible",
                "Todos los módulos de una versión promovible deben conservar "
                "navigation_path y navigation_origin_path reproducibles.",
                count=invalid,
                entity_type="module",
            )
        ]

    def _pipeline_provenance(
        self,
        version: KnowledgeVersionRecord,
    ) -> tuple[PipelineJob | None, PipelineJob | None]:
        import_job = self.session.scalar(
            select(PipelineJob)
            .where(
                PipelineJob.kind == PipelineJobKind.CANONICAL_IMPORT,
                PipelineJob.status == PipelineJobStatus.SUCCEEDED,
                PipelineJob.knowledge_version_id == version.id,
            )
            .order_by(PipelineJob.finished_at.desc(), PipelineJob.id.desc())
            .limit(1)
        )
        if import_job is None:
            return None, None
        source_id = (import_job.parameters or {}).get("source_canonical_job_id")
        try:
            source_uuid = uuid.UUID(str(source_id))
        except (TypeError, ValueError):
            return import_job, None
        source_job = self.session.get(PipelineJob, source_uuid)
        if source_job is None or source_job.kind not in {
            PipelineJobKind.CANONICAL_BUILD,
            PipelineJobKind.CANONICAL_MERGE,
        }:
            return import_job, None
        return import_job, source_job

    def _origin_import(self, version: KnowledgeVersionRecord) -> PipelineJob | None:
        jobs = list(
            self.session.scalars(
                select(PipelineJob).where(
                    PipelineJob.kind == PipelineJobKind.CANONICAL_IMPORT,
                    PipelineJob.status == PipelineJobStatus.SUCCEEDED,
                    PipelineJob.knowledge_version_id == version.id,
                )
            )
        )
        jobs = [
            job
            for job in jobs
            if dict(job.result_payload or {}).get("import_result") == "imported"
        ]
        return jobs[0] if len(jobs) == 1 else None

    def _version(self, version_id: uuid.UUID, *, for_update: bool) -> KnowledgeVersionRecord | None:
        query = select(KnowledgeVersionRecord).where(KnowledgeVersionRecord.id == version_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def _active_version(self, erp_id: str, *, for_update: bool) -> KnowledgeVersionRecord | None:
        query = select(KnowledgeVersionRecord).where(
            KnowledgeVersionRecord.erp_id == erp_id,
            KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    @staticmethod
    def _uuid(value: uuid.UUID | str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise KnowledgePromotionError("knowledge_version_id inválido") from exc

    @staticmethod
    def _assessment_payload(value: PromotionAssessment) -> dict[str, Any]:
        return {
            "knowledge_version_id": value.knowledge_version_id,
            "knowledge_version": value.knowledge_version,
            "erp_id": value.erp_id,
            "version_status": value.version_status,
            "promotable": value.promotable,
            "bootstrap_promotion": value.bootstrap_promotion,
            "promotion_mode": value.promotion_mode,
            "current_active_version_id": value.current_active_version_id,
            "current_active_knowledge_version": value.current_active_knowledge_version,
            "required_entity_types": list(value.required_entity_types),
            "required_review_counts": value.required_review_counts,
            "all_review_counts": value.all_review_counts,
            "replacement_review_counts": value.replacement_review_counts,
            "diff_totals": value.diff_totals,
            "pipeline_import_job_id": value.pipeline_import_job_id,
            "source_canonical_job_id": value.source_canonical_job_id,
            "source_reconciliation_job_id": value.source_reconciliation_job_id,
            "removal_review_set_id": value.removal_review_set_id,
            "decision_set_hash": value.decision_set_hash,
            "build_warning_count": value.build_warning_count,
            "blockers": [
                {
                    "code": blocker.code,
                    "message": blocker.message,
                    "count": blocker.count,
                    "entity_type": blocker.entity_type,
                }
                for blocker in value.blockers
            ],
            "warnings": list(value.warnings),
        }
