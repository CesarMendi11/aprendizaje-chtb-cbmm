from __future__ import annotations

import uuid
from typing import Any

from src.analysis.evidence import ScreenEvidenceBuilder
from src.analysis.eligibility import evaluate_screen_semantic_eligibility
from src.analysis.generation import OllamaStructuredGenerationClient, ScreenPurposeInferenceService
from src.analysis.generation.errors import ScreenPurposeGenerationError
from src.analysis.prompts import (
    GENERATION_PARAMETERS,
    GENERATION_PARAMETERS_HASH,
    PROMPT_HASH,
    PROMPT_VERSION,
)
from src.analysis.workflows import ScreenPurposeProposalWorkflow
from src.database.enums import KnowledgeVersionStatus, PipelineJobScope, SemanticType
from src.database.models import KnowledgeItem, KnowledgeVersionRecord
from src.database.repositories import SemanticProposalRepository
from src.database.services.semantic_exceptions import SemanticDomainError, SemanticIdentityCollisionError
from src.knowledge.canonical.enums import ReviewStatus
from src.knowledge.canonical.privacy import sanitize_text
from src.vectorstore.ollama_generation import OllamaGenerationSettings


class SemanticInferenceJobExecutionError(RuntimeError):
    pass


class SemanticInferenceJobExecutor:
    """Generate a grounded screen-purpose proposal for one approved screen in ACTIVE knowledge."""

    def __init__(
        self,
        session_factory,
        *,
        inference_service_factory=None,
        evidence_builder_factory=None,
    ):
        self.session_factory = session_factory
        self.inference_service_factory = (
            inference_service_factory or self._default_inference_service
        )
        self.evidence_builder_factory = evidence_builder_factory or ScreenEvidenceBuilder

    @staticmethod
    def _default_inference_service():
        settings = OllamaGenerationSettings()
        client = OllamaStructuredGenerationClient(
            settings=settings,
            mode="json_schema",
            timeout=max(120.0, float(settings.timeout)),
        )
        return ScreenPurposeInferenceService(client)

    def execute(self, *, job_id, scope, target, parameters, progress):
        if scope != PipelineJobScope.SCREEN:
            raise SemanticInferenceJobExecutionError(
                "La inferencia semántica requiere scope=screen"
            )
        version_id = self._uuid_parameter(parameters, "knowledge_version_id")
        screen_item_id = self._uuid_parameter(parameters, "screen_knowledge_item_id")
        expected_screen_id = str(parameters.get("screen_id") or "").strip()
        if not expected_screen_id:
            raise SemanticInferenceJobExecutionError("screen_id no puede estar vacío")

        inference_service = self.inference_service_factory()
        model = str(inference_service.client.settings.model)

        progress(
            "validating_active_screen",
            {
                "work_units": 1,
                "progress_total": 4,
                "knowledge_version_id": str(version_id),
                "screen_knowledge_item_id": str(screen_item_id),
                "screen_id": expected_screen_id,
            },
        )

        with self.session_factory() as session:
            version, screen = self._require_context(
                session,
                version_id=version_id,
                screen_item_id=screen_item_id,
                expected_screen_id=expected_screen_id,
                parameters=parameters,
            )
            builder = self.evidence_builder_factory(session)
            package = builder.build(version.id, screen.id)
            expected_evidence_hash = str(parameters.get("evidence_hash") or "").strip()
            if expected_evidence_hash != package.evidence_hash:
                raise SemanticInferenceJobExecutionError(
                    "La evidencia semántica cambió desde que el job fue encolado"
                )
            if str(parameters.get("semantic_eligibility") or "") != "eligible":
                raise SemanticInferenceJobExecutionError(
                    "El job no conserva una elegibilidad semántica certificada"
                )
            eligibility = evaluate_screen_semantic_eligibility(package)
            if not eligibility.eligible:
                progress(
                    "semantic_eligibility_rejected",
                    {
                        "work_units": 2,
                        "progress_total": 4,
                        "semantic_eligibility": eligibility.status,
                        "reasons": list(eligibility.reasons),
                        "primary_evidence": eligibility.primary_evidence_count,
                        "functional_signals": eligibility.functional_signal_count,
                    },
                )
                raise SemanticInferenceJobExecutionError(
                    "La pantalla no tiene evidencia suficiente para inferencia semántica: "
                    + ", ".join(eligibility.reasons)
                )
            existing = SemanticProposalRepository(session).get_by_generation_identity(
                knowledge_version_id=version.id,
                screen_knowledge_item_id=screen.id,
                semantic_type=SemanticType.SCREEN_PURPOSE,
                evidence_hash=package.evidence_hash,
                prompt_hash=PROMPT_HASH,
                generation_model=model,
                generation_parameters_hash=GENERATION_PARAMETERS_HASH,
            )
            if existing is not None:
                self._verify_reusable(existing, package)

        progress(
            "evidence_prepared",
            {
                "work_units": 2,
                "progress_total": 4,
                "screen_id": package.screen_id,
                "evidence_hash": package.evidence_hash,
                "fields": len(package.fields),
                "controls": len(package.controls),
                "tables": len(package.tables),
                "ui_states": len(package.ui_states),
                "events": len(package.events),
                "transitions": len(package.transitions),
                "warnings": len(package.warnings),
                "primary_evidence": eligibility.primary_evidence_count,
                "functional_signals": eligibility.functional_signal_count,
                "semantic_eligibility": eligibility.status,
            },
        )

        if existing is not None:
            progress(
                "proposal_reused",
                {
                    "work_units": 3,
                    "progress_total": 4,
                    "semantic_id": existing.semantic_id,
                    "proposal_status": str(existing.current_review_status),
                    "ollama_called": False,
                },
            )
            result = self._result(
                version=version,
                screen=screen,
                proposal=existing,
                package=package,
                created=False,
                reused_existing=True,
                ollama_called=False,
            )
        else:
            progress(
                "generating_semantic_proposal",
                {
                    "work_units": 3,
                    "progress_total": 4,
                    "generation_model": model,
                    "prompt_version": PROMPT_VERSION,
                },
            )
            try:
                candidate = inference_service.generate(package)
            except ScreenPurposeGenerationError as exc:
                self._report_generation_failure(progress, exc)
                raise self._execution_error(exc) from exc

            try:
                with self.session_factory.begin() as session:
                    workflow = ScreenPurposeProposalWorkflow(
                        session,
                        evidence_builder=self.evidence_builder_factory(session),
                        inference_service=inference_service,
                    )
                    persisted = workflow.persist_candidate(
                        version_id,
                        screen_item_id,
                        candidate,
                    )
                    proposal = SemanticProposalRepository(session).get_by_semantic_id(
                        persisted.semantic_id
                    )
                    if proposal is None:
                        raise SemanticInferenceJobExecutionError(
                            "La propuesta semántica no pudo recuperarse tras persistirla"
                        )
                    version, screen = self._require_context(
                        session,
                        version_id=version_id,
                        screen_item_id=screen_item_id,
                        expected_screen_id=expected_screen_id,
                        parameters=parameters,
                    )
                    package = self.evidence_builder_factory(session).build(version.id, screen.id)
                    result = self._result(
                        version=version,
                        screen=screen,
                        proposal=proposal,
                        package=package,
                        created=persisted.created,
                        reused_existing=persisted.reused_existing,
                        ollama_called=True,
                    )
            except ScreenPurposeGenerationError as exc:
                self._report_generation_failure(progress, exc)
                raise self._execution_error(exc) from exc
            except (SemanticDomainError, SemanticIdentityCollisionError) as exc:
                raise self._execution_error(exc) from exc

        progress(
            "proposal_ready",
            {
                "work_units": 4,
                "progress_total": 4,
                "semantic_id": result["semantic_id"],
                "proposal_status": result["proposal_status"],
                "created": result["created"],
                "reused_existing": result["reused_existing"],
                "ollama_called": result["ollama_called"],
            },
        )
        return result

    @staticmethod
    def _require_context(
        session,
        *,
        version_id: uuid.UUID,
        screen_item_id: uuid.UUID,
        expected_screen_id: str,
        parameters: dict[str, Any],
    ):
        version = session.get(KnowledgeVersionRecord, version_id)
        if version is None:
            raise SemanticInferenceJobExecutionError("Versión de conocimiento no encontrada")
        if version.status != KnowledgeVersionStatus.ACTIVE:
            raise SemanticInferenceJobExecutionError(
                "La versión dejó de ser ACTIVE; la inferencia se canceló de forma segura"
            )
        expected_version = str(parameters.get("knowledge_version") or "")
        if expected_version and version.knowledge_version != expected_version:
            raise SemanticInferenceJobExecutionError("La identidad de la versión activa cambió")

        screen = session.get(KnowledgeItem, screen_item_id)
        if screen is None:
            raise SemanticInferenceJobExecutionError("Pantalla no encontrada")
        if screen.knowledge_version_id != version.id or screen.entity_type != "screen":
            raise SemanticInferenceJobExecutionError(
                "La pantalla ya no pertenece a la versión ACTIVE capturada"
            )
        if screen.canonical_id != expected_screen_id:
            raise SemanticInferenceJobExecutionError("La identidad canónica de la pantalla cambió")
        if screen.current_review_status not in {
            ReviewStatus.APPROVED,
            ReviewStatus.CORRECTED,
        }:
            raise SemanticInferenceJobExecutionError(
                "La pantalla dejó de tener revisión estructural publicable"
            )
        return version, screen

    @staticmethod
    def _verify_reusable(proposal, package) -> None:
        expected_evidence = package.model_dump(mode="json", exclude={"evidence_hash"})
        incompatible = (
            proposal.evidence_payload != expected_evidence
            or proposal.evidence_ids != list(package.evidence_ids)
            or proposal.prompt_version != PROMPT_VERSION
            or proposal.generation_parameters != GENERATION_PARAMETERS
        )
        if incompatible:
            raise SemanticInferenceJobExecutionError(
                "La propuesta existente no coincide con la evidencia/configuración actual"
            )

    @staticmethod
    def _report_generation_failure(progress, exc: ScreenPurposeGenerationError) -> None:
        progress(
            "semantic_generation_rejected",
            {
                "work_units": 3,
                "progress_total": 4,
                "error_class": type(exc).__name__,
                "category": getattr(exc, "category", None),
                "validation_stage": getattr(exc, "stage", None),
                "location": list(getattr(exc, "location", ()) or ()),
            },
        )

    @staticmethod
    def _execution_error(exc: Exception) -> SemanticInferenceJobExecutionError:
        stage = getattr(exc, "stage", None)
        category = getattr(exc, "category", None)
        location = ".".join(getattr(exc, "location", ()) or ())
        parts = [type(exc).__name__]
        if stage:
            parts.append(str(stage))
        if category:
            parts.append(str(category))
        if location:
            parts.append(location)
        message, _ = sanitize_text(str(exc), 280)
        if message:
            parts.append(message)
        return SemanticInferenceJobExecutionError(" | ".join(parts)[:400])

    @staticmethod
    def _uuid_parameter(parameters: dict[str, Any], key: str) -> uuid.UUID:
        raw = parameters.get(key)
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError, AttributeError) as exc:
            raise SemanticInferenceJobExecutionError(f"{key} inválido") from exc

    @staticmethod
    def _result(*, version, screen, proposal, package, **flags):
        source_payload = dict(proposal.source_payload or {})
        capabilities = source_payload.get("supported_capabilities")
        return {
            "target": "semantic_proposal",
            "active_only": True,
            "semantic_type": str(proposal.semantic_type),
            "erp_id": version.erp_id,
            "knowledge_version_id": str(version.id),
            "knowledge_version": version.knowledge_version,
            "screen_knowledge_item_id": str(screen.id),
            "screen_id": screen.canonical_id,
            "screen_title": screen.title,
            "screen_route": screen.route,
            "proposal_id": str(proposal.id),
            "semantic_id": proposal.semantic_id,
            "proposal_status": str(proposal.current_review_status),
            "purpose_summary": source_payload.get("purpose_summary"),
            "capabilities": len(capabilities) if isinstance(capabilities, list) else 0,
            "evidence_hash": package.evidence_hash,
            "generation_model": proposal.generation_model,
            "prompt_version": proposal.prompt_version,
            **flags,
        }
