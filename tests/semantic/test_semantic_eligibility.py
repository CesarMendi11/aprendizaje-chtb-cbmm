from __future__ import annotations

from src.analysis.eligibility import evaluate_screen_semantic_eligibility
from src.analysis.schemas import (
    ControlEvidence,
    ModuleEvidence,
    NetworkTraceEvidence,
    ScreenEvidencePackage,
)
from src.database.services.semantic_payloads import canonical_json_hash


def package(*, primary=True, functional=True):
    values = {
        "erp_id": "erp:test",
        "knowledge_version_id": "00000000-0000-0000-0000-000000000001",
        "knowledge_version": "v1",
        "screen_id": "screen:test",
        "screen_title": "Pantalla de prueba",
        "screen_route": "/test",
        "module": ModuleEvidence(module_id="module:test", name="Módulo"),
        "controls": (
            [
                ControlEvidence(
                    control_id="control:search",
                    label="Buscar",
                    control_type="button",
                    mutative=False,
                )
            ]
            if functional
            else []
        ),
        "main_content_text": "Pantalla de prueba",
        "primary_evidence_ids": ["evidence:screen"] if primary else [],
        "evidence_ids": ["evidence:screen"] if primary else [],
        "warnings": [],
    }
    provisional = ScreenEvidencePackage.model_validate(
        {**values, "evidence_hash": "0" * 64}
    )
    digest = canonical_json_hash(
        provisional.model_dump(mode="json", exclude={"evidence_hash"})
    )
    return provisional.model_copy(update={"evidence_hash": digest})


def test_semantic_eligibility_requires_primary_evidence_and_functional_structure():
    eligible = evaluate_screen_semantic_eligibility(package())
    assert eligible.eligible is True
    assert eligible.status == "eligible"
    assert eligible.primary_evidence_count == 1
    assert eligible.functional_signal_count == 1

    no_primary = evaluate_screen_semantic_eligibility(package(primary=False))
    assert no_primary.eligible is False
    assert no_primary.reasons == ("missing_primary_evidence",)

    no_structure = evaluate_screen_semantic_eligibility(package(functional=False))
    assert no_structure.eligible is False
    assert no_structure.reasons == ("missing_functional_structure",)


def test_semantic_eligibility_reports_both_missing_conditions_deterministically():
    assessment = evaluate_screen_semantic_eligibility(
        package(primary=False, functional=False)
    )
    assert assessment.status == "insufficient_evidence"
    assert assessment.reasons == (
        "missing_primary_evidence",
        "missing_functional_structure",
    )


def test_network_evidence_does_not_create_semantic_eligibility():
    trace = NetworkTraceEvidence(
        evidence_id="evidence:network",
        methods=("GET",),
        endpoint_paths=("/api/test",),
        resource_types=("fetch",),
        origin_kinds=("same_origin",),
        status_codes=(200,),
        query_keys=(),
        observation_count=1,
        endpoint_count=1,
        read_only=True,
    )
    base = package(primary=False, functional=False)
    with_network = base.model_copy(
        update={
            "network_traces": [trace],
            "evidence_ids": [trace.evidence_id],
        }
    )

    assessment = evaluate_screen_semantic_eligibility(with_network)

    assert assessment.eligible is False
    assert assessment.primary_evidence_count == 0
    assert assessment.functional_signal_count == 0
    assert assessment.reasons == (
        "missing_primary_evidence",
        "missing_functional_structure",
    )
