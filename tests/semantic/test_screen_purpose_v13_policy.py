from __future__ import annotations

from erp_assistant.semantic.eligibility import evaluate_screen_semantic_eligibility
from erp_assistant.semantic.prompts.screen_purpose import PROMPT_VERSION, build_user_prompt
from erp_assistant.semantic.schemas import (
    ColumnEvidence,
    ControlEvidence,
    EventEvidence,
    FieldEvidence,
    ModuleEvidence,
    NetworkTraceEvidence,
    ScreenEvidencePackage,
    TableEvidence,
    TransitionEvidence,
)
from erp_assistant.semantic.services.semantic_payloads import canonical_json_hash
from erp_assistant.semantic.validators import build_grounding_plan


def package(**updates) -> ScreenEvidencePackage:
    values = {
        "erp_id": "erp:test",
        "knowledge_version_id": "00000000-0000-0000-0000-000000000001",
        "knowledge_version": "v1",
        "screen_id": "screen:test",
        "screen_title": "Pantalla de prueba",
        "screen_route": "/test",
        "module": ModuleEvidence(module_id="module:test", name="Módulo"),
        "main_content_text": "Módulo: Módulo\nPantalla: Pantalla de prueba",
        "primary_evidence_ids": ["evidence:screen"],
        "evidence_ids": ["evidence:screen"],
        "warnings": [],
    }
    values.update(updates)
    provisional = ScreenEvidencePackage.model_validate({**values, "evidence_hash": "0" * 64})
    digest = canonical_json_hash(provisional.model_dump(mode="json", exclude={"evidence_hash"}))
    return provisional.model_copy(update={"evidence_hash": digest})


def network_trace() -> NetworkTraceEvidence:
    return NetworkTraceEvidence(
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


def actions(evidence: ScreenEvidencePackage):
    return {hint.action: hint for hint in build_grounding_plan(evidence).supported_actions}


def test_v13_prompt_marks_shell_navigation_as_nonfunctional_support():
    assert PROMPT_VERSION == "screen-purpose-v13"
    prompt = build_user_prompt(
        package(
            fields=[
                FieldEvidence(
                    field_id="field:value",
                    label="Valor",
                    required=False,
                    readonly=False,
                )
            ]
        )
    )
    assert "expansión de menús" in prompt
    assert "soporte tautológico" in prompt


def test_expand_menu_event_cannot_create_search_view_or_mutative_support():
    evidence = package(
        events=[
            EventEvidence(
                event_id="event:consultar",
                label="Consultar",
                category="expand_menu",
                policy_decision="allow",
                mutative=False,
            ),
            EventEvidence(
                event_id="event:guardar",
                label="Guardar",
                category="expand_menu",
                policy_decision="allow",
                mutative=True,
            ),
        ]
    )
    assert actions(evidence) == {}


def test_search_control_without_display_structure_does_not_imply_view():
    evidence = package(
        controls=[
            ControlEvidence(
                control_id="control:search",
                label="Buscar",
                control_type="button",
                mutative=False,
            )
        ]
    )
    hints = actions(evidence)
    assert set(hints) == {"search"}
    assert "view" not in hints
    assessment = evaluate_screen_semantic_eligibility(evidence)
    assert assessment.eligible is True
    assert assessment.reasons == ()


def test_screen_and_network_alone_do_not_create_view():
    trace = network_trace()
    evidence = package(
        network_traces=[trace],
        evidence_ids=["evidence:screen", trace.evidence_id],
    )
    plan = build_grounding_plan(evidence)
    assert plan.supported_actions == ()
    assert "view" in plan.forbidden_actions


def test_field_creates_structural_view_before_screen_and_network_are_added():
    trace = network_trace()
    evidence = package(
        fields=[
            FieldEvidence(
                field_id="field:value",
                label="Valor",
                required=False,
                readonly=False,
            )
        ],
        network_traces=[trace],
        evidence_ids=["evidence:screen", trace.evidence_id],
    )
    hints = actions(evidence)
    assert set(hints) == {"view"}
    assert set(hints["view"].evidence_refs) == {
        "field:value",
        "screen:test",
        trace.evidence_id,
    }


def test_table_and_columns_create_structural_view():
    evidence = package(
        tables=[
            TableEvidence(
                table_id="table:results",
                name="Resultados",
                columns=[ColumnEvidence(column_id="table_column:name", label="NOMBRE")],
            )
        ]
    )
    refs = set(actions(evidence)["view"].evidence_refs)
    assert refs == {"screen:test", "table:results", "table_column:name"}


def test_raw_structure_without_grounded_action_fails_semantic_eligibility():
    evidence = package(
        controls=[
            ControlEvidence(
                control_id="control:update",
                label="Actualizar",
                control_type="button",
                mutative=False,
            )
        ]
    )
    assessment = evaluate_screen_semantic_eligibility(evidence)
    assert assessment.functional_signal_count == 1
    assert assessment.eligible is False
    assert assessment.reasons == ("missing_grounded_action_support",)


def test_retenciones_like_contract_preserves_search_navigate_and_view():
    evidence = package(
        fields=[
            FieldEvidence(
                field_id="field:ruc",
                label="RUC",
                required=False,
                readonly=False,
            )
        ],
        controls=[
            ControlEvidence(
                control_id="control:search",
                label="Buscar",
                control_type="button",
                mutative=False,
            )
        ],
        events=[
            EventEvidence(
                event_id="event:next",
                label="Siguiente página",
                category="change_pagination",
                policy_decision="allow",
                mutative=False,
            )
        ],
        transitions=[
            TransitionEvidence(
                transition_id="transition:next",
                category="change_pagination",
            )
        ],
    )
    hints = actions(evidence)
    assert set(hints) == {"search", "navigate", "view"}
    assert set(hints["search"].evidence_refs) == {"control:search", "field:ruc"}
    assert "event:next" in hints["navigate"].evidence_refs
    assert "transition:next" in hints["navigate"].evidence_refs
    assert set(hints["view"].evidence_refs) == {"field:ruc", "screen:test"}
