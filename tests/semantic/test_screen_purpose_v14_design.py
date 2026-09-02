from __future__ import annotations

import json

import pytest

from erp_assistant.semantic.generation.errors import (
    InferenceGroundingError,
    InferenceScreenMismatchError,
)
from erp_assistant.semantic.generation.screen_purpose_generation_v14 import (
    build_screen_purpose_generation_schema_v14,
    parse_generation_draft_v14,
)
from erp_assistant.semantic.prompts.screen_purpose_v14 import (
    GENERATION_PARAMETERS,
    PROMPT_VERSION,
    build_user_prompt_v14,
)
from erp_assistant.semantic.schemas.screen_evidence import (
    ColumnEvidence,
    ControlEvidence,
    EventEvidence,
    FieldEvidence,
    ModuleEvidence,
    NetworkTraceEvidence,
    ScreenEvidencePackage,
    TableEvidence,
    UIStateEvidence,
)
from erp_assistant.semantic.services.semantic_payloads import canonical_json_hash
from erp_assistant.semantic.validators.screen_purpose_claim_policy import (
    claimable_reference_ids,
    meaningful_semantic_signal_count,
    validate_v14_claim_references,
)


def package(**updates) -> ScreenEvidencePackage:
    values = {
        "erp_id": "erp:test",
        "knowledge_version_id": "00000000-0000-0000-0000-000000000001",
        "knowledge_version": "v1",
        "screen_id": "screen:test",
        "screen_title": "Enviar Notificaciones",
        "screen_route": "/notificaciones",
        "module": ModuleEvidence(module_id="module:test", name="Permisos"),
        "fields": [
            FieldEvidence(
                field_id="field:ruc",
                label="RUC",
                input_type="text",
                required=False,
                readonly=False,
            )
        ],
        "controls": [
            ControlEvidence(
                control_id="control:send",
                label="Enviar correo",
                control_type="button",
                mutative=True,
                safety_decision="deny",
            ),
            ControlEvidence(
                control_id="control:unlabeled",
                label="unlabeled control",
                control_type="button",
                mutative=False,
            ),
        ],
        "tables": [
            TableEvidence(
                table_id="table:results",
                name="Establecimientos",
                columns=[
                    ColumnEvidence(column_id="column:owner", label="PROPIETARIO"),
                    ColumnEvidence(column_id="column:risk", label="NIVEL DE RIESGO"),
                ],
            )
        ],
        "ui_states": [
            UIStateEvidence(state_id="state:root", title="Enviar Notificaciones", depth=0),
            UIStateEvidence(state_id="state:detail", title="Detalle", depth=1),
        ],
        "events": [
            EventEvidence(
                event_id="event:menu",
                label="Consultar",
                category="expand_menu",
                policy_decision="allow",
                mutative=False,
            ),
            EventEvidence(
                event_id="event:next",
                label="Siguiente página",
                category="change_pagination",
                policy_decision="allow",
                mutative=False,
            ),
        ],
        "network_traces": [
            NetworkTraceEvidence(
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
        ],
        "main_content_text": "Pantalla: Enviar Notificaciones",
        "primary_evidence_ids": ["evidence:screen"],
        "evidence_ids": ["evidence:screen", "evidence:network"],
        "warnings": [],
    }
    values.update(updates)
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": "0" * 64})
    digest = canonical_json_hash(provisional.model_dump(mode="json", exclude={"evidence_hash"}))
    return provisional.model_copy(update={"evidence_hash": digest})


def valid_output(**updates):
    value = {
        "semantic_type": "screen_purpose",
        "screen_id": "screen:test",
        "purpose_summary": (
            "Presenta información de establecimientos y opciones observables relacionadas "
            "con su consulta y notificación."
        ),
        "supported_capabilities": [
            {
                "statement": (
                    "Presenta datos de propietario y nivel de riesgo de los establecimientos."
                ),
                "evidence_refs": ["column:owner", "column:risk"],
            },
            {
                "statement": "La interfaz presenta la opción Enviar correo.",
                "evidence_refs": ["control:send"],
            },
        ],
        "limitations": [
            "La evidencia observada no confirma el resultado final de la acción de envío."
        ],
        "uncertainties": [],
    }
    value.update(updates)
    return value


def test_v14_prompt_delegates_semantic_interpretation_but_keeps_human_authority():
    prompt = build_user_prompt_v14(package())

    assert PROMPT_VERSION == "screen-purpose-v14"
    assert GENERATION_PARAMETERS["num_predict"] == 2048
    assert "claims funcionales libres" in prompt
    assert "revisión humana" in prompt
    assert "grounding_plan" not in prompt
    assert "supported_actions" not in prompt
    assert "control:send" in prompt
    assert "column:risk" in prompt


def test_claimable_refs_exclude_shell_network_identity_and_unlabeled_control():
    evidence = package()
    refs = set(claimable_reference_ids(evidence))

    assert "field:ruc" in refs
    assert "control:send" in refs
    assert "table:results" in refs
    assert "column:owner" in refs
    assert "column:risk" in refs
    assert "state:detail" in refs
    assert "event:next" in refs
    assert "screen:test" not in refs
    assert "module:test" not in refs
    assert "evidence:network" not in refs
    assert "control:unlabeled" not in refs
    assert "event:menu" not in refs
    assert meaningful_semantic_signal_count(evidence) == len(refs)


def test_v14_schema_allows_free_statements_but_only_claimable_references():
    evidence = package()
    schema = build_screen_purpose_generation_schema_v14(evidence)
    claim = schema["properties"]["supported_capabilities"]["items"]

    assert set(schema["required"]) == {
        "semantic_type",
        "screen_id",
        "purpose_summary",
        "supported_capabilities",
        "limitations",
        "uncertainties",
    }
    assert "action" not in claim["properties"]
    assert set(claim["properties"]) == {"statement", "evidence_refs"}
    refs = set(claim["properties"]["evidence_refs"]["items"]["enum"])
    assert "control:send" in refs
    assert "evidence:network" not in refs
    assert "event:menu" not in refs


def test_v14_parser_accepts_rich_claims_and_preserves_model_language():
    evidence = package()
    raw = json.dumps(valid_output(), ensure_ascii=False)

    inference = parse_generation_draft_v14(raw, package=evidence)

    assert inference.purpose_summary.startswith("Presenta información de establecimientos")
    assert inference.supported_capabilities[0].statement == (
        "Presenta datos de propietario y nivel de riesgo de los establecimientos."
    )
    assert inference.supported_capabilities[1].evidence_refs == ["control:send"]


def test_v14_parser_rejects_screen_mismatch():
    evidence = package()
    raw = json.dumps(valid_output(screen_id="screen:other"), ensure_ascii=False)

    with pytest.raises(InferenceScreenMismatchError):
        parse_generation_draft_v14(raw, package=evidence)


def test_v14_reference_policy_rejects_nonclaimable_reference_even_if_it_exists_in_package():
    evidence = package()
    value = valid_output()
    value["supported_capabilities"][0]["evidence_refs"] = ["evidence:network"]
    raw = json.dumps(value, ensure_ascii=False)

    with pytest.raises(InferenceGroundingError) as captured:
        parse_generation_draft_v14(raw, package=evidence)

    assert captured.value.category == "non_claimable_reference"


def test_v14_reference_policy_rejects_duplicate_claims_without_deciding_semantic_truth():
    evidence = package()
    inference = parse_generation_draft_v14(
        json.dumps(valid_output(), ensure_ascii=False),
        package=evidence,
    )
    duplicated = inference.model_copy(
        update={
            "supported_capabilities": [
                inference.supported_capabilities[0],
                inference.supported_capabilities[0],
            ]
        }
    )

    with pytest.raises(InferenceGroundingError) as captured:
        validate_v14_claim_references(duplicated, evidence)

    assert captured.value.category == "duplicate_functional_claim"


def test_v14_requires_claimable_structure_instead_of_action_vocabulary():
    evidence = package(
        fields=[],
        tables=[],
        ui_states=[],
        events=[],
        controls=[
            ControlEvidence(
                control_id="control:update",
                label="Actualizar",
                control_type="button",
                mutative=False,
            )
        ],
    )

    assert claimable_reference_ids(evidence) == ("control:update",)
    schema = build_screen_purpose_generation_schema_v14(evidence)
    assert schema["properties"]["supported_capabilities"]["items"]["properties"]["evidence_refs"][
        "items"
    ]["enum"] == ["control:update"]


def test_v14_rejects_package_with_no_claimable_semantic_evidence():
    evidence = package(
        fields=[],
        tables=[],
        ui_states=[],
        events=[
            EventEvidence(
                event_id="event:menu",
                label="Consultar",
                category="expand_menu",
                policy_decision="allow",
                mutative=False,
            )
        ],
        controls=[
            ControlEvidence(
                control_id="control:unlabeled",
                label="unlabeled control",
                control_type="button",
                mutative=False,
            )
        ],
    )

    with pytest.raises(InferenceGroundingError) as captured:
        build_screen_purpose_generation_schema_v14(evidence)

    assert captured.value.category == "no_claimable_semantic_evidence"
