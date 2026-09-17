from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from erp_assistant.config.database_settings import DatabaseSettings
from erp_assistant.integrations.ollama.generation import OllamaGenerationSettings
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus
from erp_assistant.persistence.postgres.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
)
from erp_assistant.persistence.postgres.session import create_engine_from_settings
from erp_assistant.semantic.evidence import ScreenEvidenceBuilder
from erp_assistant.semantic.generation import OllamaStructuredGenerationClient
from erp_assistant.semantic.generation.errors import ScreenPurposeGenerationError
from erp_assistant.semantic.generation.screen_purpose_service_v14 import (
    ScreenPurposeInferenceServiceV14,
)
from erp_assistant.semantic.services.semantic_exceptions import SemanticDomainError
from erp_assistant.semantic.workflows import ScreenPurposeProposalWorkflow
from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.canonical.ids import normalize_route
from scripts.experiments.common import utc_now_iso, write_json_atomic

EXPECTED_ELIGIBLE_ROUTES = 56
PUBLISHABLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


class FormalRQ2Error(RuntimeError):
    pass


def eligible_routes(
    semantic_reference: str | Path,
    policy_scope: str | Path,
) -> list[str]:
    reference = json.loads(Path(semantic_reference).read_text(encoding="utf-8"))
    policy = json.loads(Path(policy_scope).read_text(encoding="utf-8"))

    blocked = {
        normalize_route(str(route))
        for route in policy["policy_blocked_routes"]
    }
    routes = sorted(
        {
            normalize_route(str(screen["route"]))
            for screen in reference["screens"]
            if normalize_route(str(screen["route"])) not in blocked
        }
    )

    if len(routes) != EXPECTED_ELIGIBLE_ROUTES:
        raise FormalRQ2Error(
            f"Expected {EXPECTED_ELIGIBLE_ROUTES} eligible routes, got {len(routes)}"
        )
    return routes


def _active_version(session: Session) -> KnowledgeVersionRecord:
    rows = list(
        session.scalars(
            select(KnowledgeVersionRecord).where(
                KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE
            )
        )
    )
    if len(rows) != 1:
        raise FormalRQ2Error("Expected exactly one ACTIVE KnowledgeVersion")
    return rows[0]


def _screen_by_route(
    session: Session,
    *,
    version_id,
    route: str,
) -> KnowledgeItem:
    rows = list(
        session.scalars(
            select(KnowledgeItem).where(
                KnowledgeItem.knowledge_version_id == version_id,
                KnowledgeItem.entity_type == "screen",
                KnowledgeItem.route == route,
                KnowledgeItem.current_review_status.in_(PUBLISHABLE),
            )
        )
    )
    if len(rows) != 1:
        raise FormalRQ2Error(f"Expected one governed screen for route: {route}")
    return rows[0]


def _require_formal_target() -> None:
    if os.getenv("ERP_ASSISTANT_FORMAL_RUN") != "1":
        raise FormalRQ2Error("Set ERP_ASSISTANT_FORMAL_RUN=1 for formal execution")

    url = DatabaseSettings().require_url()
    if "formal003" not in url.casefold():
        raise FormalRQ2Error("ERP_ASSISTANT_DATABASE_URL does not identify FORMAL003")


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser()
    command.add_argument("--semantic-reference", default=str(
        Path("experiments/gold_standard/formal/rq2/semantic_reference_v1.json")
    ))
    command.add_argument("--policy-scope", default=str(
        Path("experiments/protocol/policy_scope_contract_v1.json")
    ))
    command.add_argument("--output", required=True)
    command.add_argument("--validate-only", action="store_true")
    return command


def main() -> int:
    args = parser().parse_args()
    routes = eligible_routes(args.semantic_reference, args.policy_scope)

    if args.validate_only:
        print(json.dumps({"eligible_routes": len(routes)}, sort_keys=True))
        return 0

    _require_formal_target()

    engine = create_engine_from_settings(DatabaseSettings())
    output = Path(args.output)
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "execution": "FORMAL003_RQ2_GENERATION",
        "created_at": utc_now_iso(),
        "eligible_routes": len(routes),
        "automatic_retry": False,
        "records": [],
        "status": "running",
    }
    write_json_atomic(output, report)

    try:
        with Session(engine) as session:
            version = _active_version(session)
            existing = session.scalar(
                select(func.count(SemanticProposal.id)).where(
                    SemanticProposal.knowledge_version_id == version.id
                )
            )
            if existing:
                raise FormalRQ2Error(
                    "SemanticProposal rows already exist for the ACTIVE version"
                )

        for route in routes:
            started = time.perf_counter()
            record: dict[str, Any] = {"route": route, "attempts": 1}

            try:
                with Session(engine, expire_on_commit=False) as session:
                    version = _active_version(session)
                    screen = _screen_by_route(
                        session,
                        version_id=version.id,
                        route=route,
                    )
                    workflow = ScreenPurposeProposalWorkflow(
                        session,
                        evidence_builder=ScreenEvidenceBuilder(session),
                        inference_service=ScreenPurposeInferenceServiceV14(
                            OllamaStructuredGenerationClient(
                                settings=OllamaGenerationSettings(),
                                mode="json_schema",
                            )
                        ),
                    )
                    result = workflow.generate_and_persist(version.id, screen.id)
                    session.commit()
                    record.update(
                        {
                            "outcome": "proposal_created",
                            "semantic_id": result.semantic_id,
                            "proposal_id": str(result.proposal_id),
                            "status": str(result.status),
                            "ollama_called": result.ollama_called,
                        }
                    )
            except (ScreenPurposeGenerationError, SemanticDomainError) as exc:
                record.update(
                    {
                        "outcome": "not_available",
                        "error_class": type(exc).__name__,
                        "category": getattr(exc, "category", None),
                        "stage": getattr(exc, "stage", None),
                    }
                )
            except Exception as exc:
                record.update(
                    {
                        "outcome": "unexpected_error",
                        "error_class": type(exc).__name__,
                    }
                )
                record["elapsed_ms"] = round(
                    (time.perf_counter() - started) * 1000,
                    2,
                )
                report["records"].append(record)
                report["status"] = "aborted"
                write_json_atomic(output, report)
                raise

            record["elapsed_ms"] = round(
                (time.perf_counter() - started) * 1000,
                2,
            )
            report["records"].append(record)
            write_json_atomic(output, report)

        report["status"] = "complete"
        report["summary"] = {
            "routes": len(report["records"]),
            "proposal_created": sum(
                row["outcome"] == "proposal_created"
                for row in report["records"]
            ),
            "not_available": sum(
                row["outcome"] == "not_available"
                for row in report["records"]
            ),
        }
        write_json_atomic(output, report)
        print(json.dumps(report["summary"], sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
