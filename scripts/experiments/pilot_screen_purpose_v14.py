from __future__ import annotations

import argparse

from sqlalchemy import func, select

from erp_assistant.integrations.ollama.generation import OllamaGenerationSettings
from erp_assistant.persistence.postgres.enums import KnowledgeVersionStatus
from erp_assistant.persistence.postgres.models import (
    KnowledgeItem,
    KnowledgeVersionRecord,
    SemanticProposal,
    SemanticReviewAction,
)
from erp_assistant.persistence.postgres.session import session_scope
from erp_assistant.semantic.evidence import ScreenEvidenceBuilder
from erp_assistant.semantic.generation.ollama_structured_client import (
    OllamaStructuredGenerationClient,
)
from erp_assistant.semantic.generation.screen_purpose_service_v14 import (
    ScreenPurposeInferenceServiceV14,
)
from erp_assistant.semantic.schemas.screen_purpose_prompt_evidence_v14 import (
    ScreenPurposePromptEvidenceV14,
)
from erp_assistant.semantic.validators.screen_purpose_claim_policy import (
    claimable_reference_index,
)
from erp_assistant.structural.canonical.enums import ReviewStatus
from scripts.common.database import database_engine, print_json

ELIGIBLE = {ReviewStatus.APPROVED, ReviewStatus.CORRECTED}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta screen-purpose-v14 contra pantallas ACTIVE sin crear SemanticProposal "
            "ni modificar Semantic Chroma."
        )
    )
    parser.add_argument(
        "--screen-id",
        action="append",
        required=True,
        dest="screen_ids",
        help="canonical_id de una Screen ACTIVE; se puede repetir",
    )
    parser.add_argument(
        "--model",
        help="modelo Ollama para el piloto; por defecto usa ERP_ASSISTANT_GENERATION_MODEL",
    )
    return parser.parse_args(argv)


def _active_version(session) -> KnowledgeVersionRecord:
    versions = list(
        session.scalars(
            select(KnowledgeVersionRecord).where(
                KnowledgeVersionRecord.status == KnowledgeVersionStatus.ACTIVE
            )
        )
    )
    if len(versions) != 1:
        raise RuntimeError("El piloto requiere exactamente una KnowledgeVersion ACTIVE")
    return versions[0]


def _screen(session, version_id, canonical_id: str) -> KnowledgeItem:
    item = session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.knowledge_version_id == version_id,
            KnowledgeItem.entity_type == "screen",
            KnowledgeItem.canonical_id == canonical_id,
        )
    )
    if item is None:
        raise RuntimeError(f"Screen ACTIVE no encontrada: {canonical_id}")
    if item.current_review_status not in ELIGIBLE:
        raise RuntimeError(f"Screen no publicable: {canonical_id}")
    return item


def _semantic_counts(session) -> dict[str, int]:
    return {
        "semantic_proposals": int(
            session.scalar(select(func.count()).select_from(SemanticProposal)) or 0
        ),
        "semantic_review_actions": int(
            session.scalar(select(func.count()).select_from(SemanticReviewAction)) or 0
        ),
    }


def _settings(model: str | None) -> OllamaGenerationSettings:
    base = OllamaGenerationSettings()
    if not model:
        return base
    return OllamaGenerationSettings(
        url=base.url,
        model=model,
        timeout=base.timeout,
        structured_timeout=base.structured_timeout,
    )


def run(screen_ids: list[str], *, model: str | None = None):
    requested = [value.strip() for value in screen_ids if str(value).strip()]
    if not requested or len(set(requested)) != len(requested):
        raise ValueError("--screen-id requiere IDs no vacíos y sin duplicados")

    settings = _settings(model)
    client = OllamaStructuredGenerationClient(settings)
    inference = ScreenPurposeInferenceServiceV14(client)

    with session_scope(database_engine()) as session:
        version = _active_version(session)
        before = _semantic_counts(session)
        builder = ScreenEvidenceBuilder(session)
        results = []

        for canonical_id in requested:
            item = _screen(session, version.id, canonical_id)
            package = builder.build(version.id, item.id)
            prompt_evidence = ScreenPurposePromptEvidenceV14.from_package(package)
            claimable = claimable_reference_index(package)
            row = {
                "screen_id": package.screen_id,
                "screen_title": package.screen_title,
                "screen_route": package.screen_route,
                "evidence_hash": package.evidence_hash,
                "evidence_summary": {
                    "fields": len(prompt_evidence.fields),
                    "controls": len(prompt_evidence.controls),
                    "tables": len(prompt_evidence.tables),
                    "columns": sum(len(table.columns) for table in prompt_evidence.tables),
                    "ui_states": len(prompt_evidence.ui_states),
                    "events": len(prompt_evidence.events),
                    "transitions": len(prompt_evidence.transitions),
                    "read_only_network_traces": len(prompt_evidence.network_traces),
                    "claimable_references": len(claimable),
                    "claimable_by_type": {
                        kind: sum(1 for value in claimable.values() if value["type"] == kind)
                        for kind in sorted({value["type"] for value in claimable.values()})
                    },
                },
            }
            try:
                generated = inference.generate(package)
            except Exception as exc:  # pilot must preserve the other screen results
                row["status"] = "generation_failed"
                row["error_type"] = type(exc).__name__
                row["error"] = str(exc)[:500]
            else:
                candidate = generated.inference
                row.update(
                    {
                        "status": "generated",
                        "generation_model": generated.generation_model,
                        "prompt_version": generated.prompt_version,
                        "prompt_hash": generated.prompt_hash,
                        "generation_parameters_hash": generated.generation_parameters_hash,
                        "structured_output_mode": generated.structured_output_mode,
                        "generated_content_hash": generated.generated_content_hash,
                        "raw_response_hash": generated.raw_response_hash,
                        "purpose_summary": candidate.purpose_summary,
                        "functional_claims": [
                            {
                                "statement": claim.statement,
                                "evidence_refs": claim.evidence_refs,
                            }
                            for claim in candidate.supported_capabilities
                        ],
                        "limitations": candidate.limitations,
                        "uncertainties": candidate.uncertainties,
                        "warnings": generated.warnings,
                    }
                )
            results.append(row)

        after = _semantic_counts(session)
        if before != after or session.new or session.dirty or session.deleted:
            raise RuntimeError("El piloto v14 detectó una mutación inesperada de persistencia")

        return {
            "mode": "read_only_v14_pilot",
            "knowledge_version": version.knowledge_version,
            "knowledge_version_id": str(version.id),
            "generation_model": settings.model,
            "screens_requested": len(requested),
            "semantic_persistence_before": before,
            "semantic_persistence_after": after,
            "semantic_persistence_unchanged": True,
            "results": results,
        }


def main(argv=None):
    args = parse_args(argv)
    try:
        print_json(run(args.screen_ids, model=args.model), pretty=True)
        return 0
    except Exception as exc:
        print_json(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            },
            pretty=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
