from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.enums import (
    KnowledgeVersionStatus,
    PipelineJobKind,
    PipelineJobScope,
    PipelineJobStatus,
)
from src.database.models import KnowledgeItem, KnowledgeVersionRecord, PipelineJob


class VersionDiffError(ValueError):
    """The candidate cannot be proven to be a governed FULL snapshot."""


class VersionDiffChangeType(StrEnum):
    UNCHANGED = "unchanged"
    MODIFIED = "modified"
    NEW = "new"
    REMOVED = "removed"


@dataclass(frozen=True)
class VersionDiffItem:
    change_type: VersionDiffChangeType
    entity_type: str
    canonical_id: str
    active_item_id: str | None
    candidate_item_id: str | None
    active_content_hash: str | None
    candidate_content_hash: str | None
    active_review_status: str | None
    candidate_review_status: str | None
    active_title: str | None
    candidate_title: str | None
    active_route: str | None
    candidate_route: str | None


@dataclass(frozen=True)
class VersionDiff:
    active_version_id: str
    active_knowledge_version: str
    candidate_version_id: str
    candidate_knowledge_version: str
    erp_id: str
    totals: dict[str, int]
    counts_by_entity_type: dict[str, dict[str, int]]
    items: tuple[VersionDiffItem, ...]


class VersionDiffService:
    """Deterministic, read-only structural diff of ACTIVE against a FULL candidate."""

    def __init__(self, session: Session):
        self.session = session

    def compare(
        self,
        candidate_version_id: uuid.UUID | str,
        *,
        change_type: VersionDiffChangeType | str | None = None,
        entity_type: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> VersionDiff:
        candidate_id = self._uuid(candidate_version_id)
        candidate = self.session.get(KnowledgeVersionRecord, candidate_id)
        if candidate is None:
            raise LookupError("KnowledgeVersion no encontrada")
        if candidate.status != KnowledgeVersionStatus.IMPORTED:
            raise VersionDiffError("El candidate debe tener status IMPORTED.")

        active_versions = list(
            self.session.scalars(
                select(KnowledgeVersionRecord).where(
                    KnowledgeVersionRecord.erp_id == candidate.erp_id,
                    KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
                )
            )
        )
        if len(active_versions) != 1:
            raise VersionDiffError(
                "Debe existir exactamente una KnowledgeVersion ACTIVE para el ERP."
            )
        active = active_versions[0]
        if active.id == candidate.id:
            raise VersionDiffError("ACTIVE y candidate deben ser versiones distintas.")

        self._validate_governed_full_provenance(candidate)
        all_items = self._items(active, candidate)
        totals, by_type = self._counts(all_items)
        try:
            requested_type = VersionDiffChangeType(change_type) if change_type is not None else None
        except ValueError as exc:
            raise VersionDiffError("change_type inválido") from exc
        filtered = tuple(
            item
            for item in all_items
            if (
                (requested_type is None or item.change_type == requested_type)
                and (entity_type is None or item.entity_type == entity_type)
            )
        )
        start = max(offset, 0)
        page = filtered[start:] if limit is None else filtered[start : start + limit]
        return VersionDiff(
            active_version_id=str(active.id),
            active_knowledge_version=active.knowledge_version,
            candidate_version_id=str(candidate.id),
            candidate_knowledge_version=candidate.knowledge_version,
            erp_id=candidate.erp_id,
            totals=totals,
            counts_by_entity_type=by_type,
            items=page,
        )

    def _validate_governed_full_provenance(self, candidate: KnowledgeVersionRecord) -> None:
        imports = list(
            self.session.scalars(
                select(PipelineJob).where(
                    PipelineJob.kind == PipelineJobKind.CANONICAL_IMPORT,
                    PipelineJob.status == PipelineJobStatus.SUCCEEDED,
                    PipelineJob.knowledge_version_id == candidate.id,
                )
            )
        )
        origin_imports = [
            job
            for job in imports
            if dict(job.result_payload or {}).get("import_result") == "imported"
        ]
        if len(origin_imports) != 1:
            raise VersionDiffError("La provenance canonical_import gobernada es ausente o ambigua.")
        import_job = origin_imports[0]
        parameters = dict(import_job.parameters or {})
        result = dict(import_job.result_payload or {})
        if (
            import_job.scope != PipelineJobScope.FULL
            or import_job.erp_id != candidate.erp_id
            or parameters.get("activation_mode") != "staging_only"
            or result.get("staging_ready") is not True
            or result.get("activation_performed") is not False
            or result.get("knowledge_version") != candidate.knowledge_version
            or str(result.get("knowledge_version_id") or "") != str(candidate.id)
        ):
            raise VersionDiffError(
                "La provenance canonical_import no demuestra un candidate gobernado."
            )
        try:
            source_id = uuid.UUID(str(parameters.get("source_canonical_job_id")))
        except (TypeError, ValueError) as exc:
            raise VersionDiffError("Falta la provenance del canonical fuente.") from exc
        source = self.session.get(PipelineJob, source_id)
        if source is None or source.kind not in {
            PipelineJobKind.CANONICAL_BUILD,
            PipelineJobKind.CANONICAL_MERGE,
        }:
            raise VersionDiffError("La provenance del canonical fuente es inválida.")
        source_result = dict(source.result_payload or {})
        if (
            source.status != PipelineJobStatus.SUCCEEDED
            or source_result.get("snapshot_mode") != "full"
            or source_result.get("snapshot_scope") != "full"
            or source_result.get("knowledge_version") != candidate.knowledge_version
        ):
            raise VersionDiffError(
                "El canonical fuente no demuestra snapshot_mode=full y snapshot_scope=full."
            )

    def _items(
        self, active: KnowledgeVersionRecord, candidate: KnowledgeVersionRecord
    ) -> tuple[VersionDiffItem, ...]:
        active_by_key = {
            (item.entity_type, item.canonical_id): item
            for item in self.session.scalars(
                select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == active.id)
            )
        }
        candidate_by_key = {
            (item.entity_type, item.canonical_id): item
            for item in self.session.scalars(
                select(KnowledgeItem).where(KnowledgeItem.knowledge_version_id == candidate.id)
            )
        }
        result: list[VersionDiffItem] = []
        for key in sorted(set(active_by_key) | set(candidate_by_key)):
            active_item = active_by_key.get(key)
            candidate_item = candidate_by_key.get(key)
            if active_item is None:
                kind = VersionDiffChangeType.NEW
            elif candidate_item is None:
                kind = VersionDiffChangeType.REMOVED
            elif active_item.content_hash == candidate_item.content_hash:
                kind = VersionDiffChangeType.UNCHANGED
            else:
                kind = VersionDiffChangeType.MODIFIED
            result.append(
                VersionDiffItem(
                    change_type=kind,
                    entity_type=key[0],
                    canonical_id=key[1],
                    active_item_id=str(active_item.id) if active_item else None,
                    candidate_item_id=str(candidate_item.id) if candidate_item else None,
                    active_content_hash=active_item.content_hash if active_item else None,
                    candidate_content_hash=candidate_item.content_hash if candidate_item else None,
                    active_review_status=(
                        str(active_item.current_review_status) if active_item else None
                    ),
                    candidate_review_status=(
                        str(candidate_item.current_review_status) if candidate_item else None
                    ),
                    active_title=active_item.title if active_item else None,
                    candidate_title=candidate_item.title if candidate_item else None,
                    active_route=active_item.route if active_item else None,
                    candidate_route=candidate_item.route if candidate_item else None,
                )
            )
        return tuple(result)

    @staticmethod
    def _counts(
        items: tuple[VersionDiffItem, ...],
    ) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
        totals = Counter(item.change_type.value for item in items)
        by_type: dict[str, Counter[str]] = {}
        for item in items:
            by_type.setdefault(item.entity_type, Counter())[item.change_type.value] += 1
        all_types = tuple(change.value for change in VersionDiffChangeType)
        return (
            {kind: totals[kind] for kind in all_types},
            {
                entity: {kind: counter[kind] for kind in all_types}
                for entity, counter in sorted(by_type.items())
            },
        )

    @staticmethod
    def _uuid(value: uuid.UUID | str) -> uuid.UUID:
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise VersionDiffError("candidate_version_id inválido") from exc
