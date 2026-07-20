from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from src.database.enums import (
    ImportStatus,
    KnowledgeVersionStatus,
    ReviewActionType,
    ReviewSource,
    SyncStatus,
    SyncTarget,
)
from src.database.models import (
    ERPSystemRecord,
    ImportRun,
    KnowledgeItem,
    KnowledgeVersionRecord,
    ReviewAction,
    SyncJob,
)
from src.database.repositories import KnowledgeRepository, ReviewRepository
from src.database.types import utcnow
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.privacy import safe_metadata
from src.knowledge.canonical.repository import CanonicalKnowledgeRepository
from src.knowledge.canonical.validator import CanonicalKnowledgeValidator

from .payloads import item_content_hash

COLLECTION_TYPES = {
    "modules": "module",
    "screens": "screen",
    "ui_states": "ui_state",
    "fields": "field",
    "controls": "control",
    "tables": "table",
    "table_columns": "table_column",
    "links": "link",
    "events": "event",
    "transitions": "transition",
    "evidence": "evidence",
}
PARENT_KEYS = (
    "module_id",
    "screen_id",
    "table_id",
    "source_state_id",
    "erp_id",
)


@dataclass(frozen=True)
class ImportResult:
    result: str
    knowledge_version: str
    items: int
    carried_reviews: int
    warnings: int
    duration_seconds: float
    version_id: str | None = None


class CanonicalImportService:
    def __init__(self, session: Session):
        self.session = session

    def dry_run(
        self, knowledge_path: Path | str, manifest_path: Path | str, build_report_path=None
    ) -> ImportResult:
        started = time.monotonic()
        knowledge, manifest, report = self._load(knowledge_path, manifest_path, build_report_path)
        return ImportResult(
            "dry_run",
            knowledge.knowledge_version,
            1 + sum(len(getattr(knowledge, name)) for name in COLLECTION_TYPES),
            0,
            len(report.get("warnings", knowledge.build_warnings)),
            time.monotonic() - started,
        )

    def import_canonical(
        self,
        knowledge_path: Path | str,
        manifest_path: Path | str,
        build_report_path: Path | str | None = None,
        *,
        activate: bool = True,
        create_sync_jobs: bool = True,
    ) -> ImportResult:
        started = time.monotonic()
        knowledge, manifest, report = self._load(knowledge_path, manifest_path, build_report_path)
        erp = knowledge.erp_system
        record = self.session.get(ERPSystemRecord, erp.id)
        values = dict(
            slug=erp.slug,
            name=erp.name,
            profile_name=erp.profile_name,
            base_url=erp.base_url,
            adapter=erp.adapter,
            safe_metadata=safe_metadata(erp.metadata),
        )
        if record is None:
            record = ERPSystemRecord(id=erp.id, **values)
            self.session.add(record)
        else:
            for key, value in values.items():
                setattr(record, key, value)
        self.session.flush()
        run = ImportRun(
            erp_id=erp.id,
            source_knowledge_path=str(Path(knowledge_path)),
            source_manifest_path=str(Path(manifest_path)),
            requested_knowledge_version=knowledge.knowledge_version,
            status=ImportStatus.RUNNING,
            warning_count=len(report.get("warnings", [])),
            source_hashes=knowledge.source_artifact_hashes,
        )
        self.session.add(run)
        self.session.flush()
        repo = KnowledgeRepository(self.session)
        existing = repo.get_version(erp.id, knowledge.knowledge_version)
        if existing:
            run.status = ImportStatus.SKIPPED
            run.skipped_items = len(existing.items)
            run.finished_at = utcnow()
            return ImportResult(
                "skipped",
                knowledge.knowledge_version,
                len(existing.items),
                0,
                run.warning_count,
                time.monotonic() - started,
                str(existing.id),
            )
        previous = repo.get_active_version(erp.id)
        version = KnowledgeVersionRecord(
            erp_id=erp.id,
            import_run_id=run.id,
            schema_version=knowledge.schema_version,
            knowledge_version=knowledge.knowledge_version,
            canonical_hash=manifest["canonical_document_hash"],
            generated_at=knowledge.generated_at,
            entity_counts=manifest.get("entity_counts", knowledge.statistics),
            source_artifact_hashes=manifest.get("source_artifact_hashes", {}),
            build_warnings=report.get("warnings", []),
            status=KnowledgeVersionStatus.IMPORTED,
        )
        self.session.add(version)
        self.session.flush()
        old_items = {}
        if previous:
            old_items = {(item.entity_type, item.canonical_id): item for item in previous.items}
        carried = 0
        for entity_type, payload in self._items(knowledge):
            old = old_items.get((entity_type, payload["id"]))
            digest = item_content_hash(payload)
            status = ReviewStatus(payload.get("review_status", ReviewStatus.PENDING_REVIEW))
            if old and old.content_hash == digest:
                status = old.current_review_status
            item = KnowledgeItem(
                knowledge_version_id=version.id,
                canonical_id=payload["id"],
                entity_type=entity_type,
                parent_canonical_id=next(
                    (payload[key] for key in PARENT_KEYS if payload.get(key)), None
                ),
                title=self._first(payload, "title", "name", "label"),
                normalized_title=self._first(
                    payload, "normalized_title", "normalized_name", "normalized_label"
                ),
                route=self._first(payload, "route", "target_route"),
                content_hash=digest,
                source_payload=payload,
                generated_review_status=ReviewStatus(
                    payload.get("review_status", ReviewStatus.PENDING_REVIEW)
                ),
                current_review_status=status,
            )
            self.session.add(item)
            self.session.flush()
            if old and old.content_hash == digest and (
                old.current_review_status != ReviewStatus.PENDING_REVIEW
                or ReviewRepository(self.session).latest_correction(old.id)
            ):
                correction = ReviewRepository(self.session).latest_correction(old.id)
                self.session.add(
                    ReviewAction(
                        knowledge_item_id=item.id,
                        previous_item_id=old.id,
                        action=(
                            ReviewActionType.CORRECT
                            if correction
                            else ReviewActionType.APPROVE
                            if status == ReviewStatus.APPROVED
                            else ReviewActionType.REJECT
                        ),
                        previous_status=ReviewStatus.PENDING_REVIEW,
                        new_status=status,
                        corrected_payload=correction.corrected_payload if correction else None,
                        review_notes="Revisión arrastrada sin cambios funcionales",
                        item_content_hash=digest,
                        source=ReviewSource.CARRY_FORWARD,
                    )
                )
                item.review_revision = 1
                carried += 1
        if activate:
            if previous:
                previous.status = KnowledgeVersionStatus.ARCHIVED
            version.status = KnowledgeVersionStatus.ACTIVE
        if activate and create_sync_jobs:
            for target in SyncTarget:
                self.session.add(
                    SyncJob(
                        knowledge_version_id=version.id,
                        target=target,
                        status=SyncStatus.PENDING,
                    )
                )
        run.status = ImportStatus.SUCCEEDED
        run.inserted_items = 1 + sum(len(getattr(knowledge, name)) for name in COLLECTION_TYPES)
        run.carried_reviews = carried
        run.finished_at = utcnow()
        return ImportResult(
            "imported",
            knowledge.knowledge_version,
            run.inserted_items,
            carried,
            run.warning_count,
            time.monotonic() - started,
            str(version.id),
        )

    @staticmethod
    def _load(knowledge_path, manifest_path, report_path):
        knowledge = CanonicalKnowledgeRepository(knowledge_path).knowledge
        errors = CanonicalKnowledgeValidator().errors(knowledge)
        if errors:
            raise ValueError(f"Conocimiento canónico inválido: {len(errors)} errores")
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        report = (
            json.loads(Path(report_path).read_text(encoding="utf-8")) if report_path else {}
        )
        if manifest.get("knowledge_version") != knowledge.knowledge_version:
            raise ValueError("manifest.json no corresponde a knowledge.json")
        calculated = content_hash(knowledge.model_dump(mode="json"))
        if manifest.get("canonical_document_hash") != calculated:
            raise ValueError("Hash canónico del manifest no coincide")
        return knowledge, manifest, report

    @staticmethod
    def _items(knowledge: CanonicalKnowledgeBase):
        yield "erp_system", knowledge.erp_system.model_dump(mode="json")
        for collection, entity_type in COLLECTION_TYPES.items():
            for item in getattr(knowledge, collection):
                yield entity_type, item.model_dump(mode="json")

    @staticmethod
    def _first(payload, *keys):
        return next((payload[key] for key in keys if payload.get(key)), None)
