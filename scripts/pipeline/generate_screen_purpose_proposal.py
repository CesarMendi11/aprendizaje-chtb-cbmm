#!/usr/bin/env python3
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from erp_assistant.config.paths import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from erp_assistant.config.database_settings import DatabaseSettings
from erp_assistant.integrations.ollama.generation import OllamaGenerationSettings
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus
from erp_assistant.persistence.postgres.models import KnowledgeItem, KnowledgeVersionRecord
from erp_assistant.semantic.evidence import ScreenEvidenceBuilder
from erp_assistant.semantic.generation import OllamaStructuredGenerationClient
from erp_assistant.semantic.generation.errors import ScreenPurposeGenerationError
from erp_assistant.semantic.generation.screen_purpose_service_v14 import (
    ScreenPurposeInferenceServiceV14,
)
from erp_assistant.semantic.services.semantic_exceptions import SemanticDomainError
from erp_assistant.semantic.workflows import ScreenPurposeProposalWorkflow
from erp_assistant.structural.canonical.enums import ReviewStatus


class CLIError(RuntimeError):
    def __init__(self, category: str, *, stage: str = "preflight"):
        super().__init__(category)
        self.category = category
        self.stage = stage


@dataclass
class ExecutionState:
    ollama_called: bool = False
    persisted: bool = False


EXECUTION_STATE = ExecutionState()


class TrackedOllamaStructuredGenerationClient(OllamaStructuredGenerationClient):
    def generate(self, *args, **kwargs):
        EXECUTION_STATE.ollama_called = True
        return super().generate(*args, **kwargs)


def _load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def _database_url():
    return make_url(DatabaseSettings().require_url())


def _screen(session: Session, *, title: str | None, canonical_id: str | None):
    query = (
        select(KnowledgeVersionRecord, KnowledgeItem)
        .join(KnowledgeItem, KnowledgeItem.knowledge_version_id == KnowledgeVersionRecord.id)
        .where(
            KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE,
            KnowledgeItem.entity_type == "screen",
            KnowledgeItem.current_review_status.in_(
                [ReviewStatus.APPROVED, ReviewStatus.CORRECTED]
            ),
        )
    )
    query = (
        query.where(KnowledgeItem.canonical_id == canonical_id)
        if canonical_id
        else query.where(func.lower(KnowledgeItem.title) == str(title).strip().casefold())
    )
    rows = session.execute(query).all()
    if len(rows) != 1:
        raise CLIError("screen_selection_not_unique", stage="selection")
    return rows[0]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Genera una propuesta screen_purpose pendiente")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--screen-title")
    selector.add_argument("--screen-id")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--persist", action="store_true")
    parser.add_argument("--confirm-persist", action="store_true")
    return parser


def _semantic_schema_preflight(connection) -> None:
    revision_table = connection.exec_driver_sql(
        "SELECT to_regclass('public.alembic_version')"
    ).scalar_one()
    if revision_table is None:
        raise CLIError("semantic_schema_not_applied")
    revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    proposals = connection.exec_driver_sql(
        "SELECT to_regclass('public.semantic_proposals')"
    ).scalar_one()
    actions = connection.exec_driver_sql(
        "SELECT to_regclass('public.semantic_review_actions')"
    ).scalar_one()
    if revision != "20260721_01" or proposals is None or actions is None:
        raise CLIError("semantic_schema_not_applied")


def main() -> int:
    args = _parser().parse_args()
    if args.persist and not args.confirm_persist:
        raise CLIError("persist_confirmation_required")
    _load_env()
    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if not args.persist:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    read_only = connection.exec_driver_sql(
                        "SHOW transaction_read_only"
                    ).scalar_one()
                    if read_only != "on":
                        raise CLIError("dry_run_transaction_not_read_only")
                database_name = connection.exec_driver_sql("SELECT current_database()").scalar_one()
                expected_database = engine.url.database
                if not expected_database or database_name != expected_database:
                    raise CLIError("database_identity_mismatch")
                if args.persist:
                    _semantic_schema_preflight(connection)
                session = Session(bind=connection, autoflush=False, expire_on_commit=False)
                version, screen = _screen(
                    session,
                    title=args.screen_title,
                    canonical_id=args.screen_id,
                )
                ollama = OllamaGenerationSettings()
                tags = httpx.get(
                    f"{ollama.url}/api/tags",
                    timeout=min(ollama.timeout, 5.0),
                )
                tags.raise_for_status()
                installed = {item.get("name") for item in tags.json().get("models", [])}
                if ollama.model not in installed:
                    raise CLIError("generation_model_not_installed")
                inference = ScreenPurposeInferenceServiceV14(
                    TrackedOllamaStructuredGenerationClient(
                        settings=ollama,
                        mode="json_schema",
                    )
                )
                workflow = ScreenPurposeProposalWorkflow(
                    session,
                    evidence_builder=ScreenEvidenceBuilder(session),
                    inference_service=inference,
                )
                if args.persist:
                    result = workflow.generate_and_persist(version.id, screen.id)
                    session.flush()
                    output = result.model_dump(mode="json")
                    transaction.commit()
                    EXECUTION_STATE.persisted = True
                    output["persisted"] = True
                    output["ollama_called"] = EXECUTION_STATE.ollama_called
                else:
                    candidate = workflow.generate_candidate(version.id, screen.id)
                    output = {
                        "mode": "dry_run",
                        "screen_id": candidate.inference.screen_id,
                        "semantic_type": candidate.inference.semantic_type,
                        "status": "not_persisted",
                        "inference": candidate.inference.model_dump(mode="json"),
                        "evidence_ids": list(candidate.evidence_ids),
                        "warnings": list(candidate.warnings),
                        "structured_output_mode": candidate.structured_output_mode,
                        "prompt_version": candidate.prompt_version,
                        "prompt_hash": candidate.prompt_hash,
                        "generation_model": candidate.generation_model,
                        "generation_parameters_hash": candidate.generation_parameters_hash,
                        "evidence_hash": candidate.evidence_hash,
                        "generated_content_hash": candidate.generated_content_hash,
                        "ollama_called": EXECUTION_STATE.ollama_called,
                        "persisted": False,
                    }
                    transaction.rollback()
                print(json.dumps(output, ensure_ascii=False, sort_keys=True))
            except Exception:
                if transaction.is_active:
                    transaction.rollback()
                raise
    finally:
        engine.dispose()
    return 0


def cli_main() -> int:
    EXECUTION_STATE.ollama_called = False
    EXECUTION_STATE.persisted = False
    try:
        return main()
    except Exception as exc:  # noqa: BLE001 - CLI trust boundary
        expected = isinstance(exc, (CLIError, ScreenPurposeGenerationError, SemanticDomainError))
        diagnostic = {
            "ok": False,
            "stage": getattr(exc, "stage", "workflow" if expected else "internal"),
            "error_class": type(exc).__name__,
            "category": getattr(
                exc,
                "category",
                "workflow_error" if expected else "unexpected_internal_error",
            ),
            "location": list(getattr(exc, "location", ()) or ()),
            "value_length": getattr(exc, "value_length", None),
            "value_type": getattr(exc, "value_type", None),
            "structured_output_mode": "json_schema",
            "persisted": EXECUTION_STATE.persisted,
            "ollama_called": EXECUTION_STATE.ollama_called,
        }
        print(json.dumps(diagnostic, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli_main())
