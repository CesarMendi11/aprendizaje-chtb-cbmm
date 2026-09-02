from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from erp_assistant.semantic.generation.errors import (
    InferenceGroundingError,
    InferenceSchemaError,
    InferenceUnsupportedActionError,
)
from erp_assistant.semantic.generation.screen_purpose_generation import (
    GeneratedCapabilityDraft,
    ScreenPurposeGenerationDraft,
    build_deterministic_purpose_summary,
    build_screen_purpose_generation_schema,
    parse_generation_draft,
)
from erp_assistant.semantic.schemas import ActionGroundingHint, ScreenPurposeGroundingPlan


def hint(action, refs, *, narrative_rule="direct_allowed"):
    return ActionGroundingHint(
        action=action,
        support_level="direct" if narrative_rule == "direct_allowed" else "prudent_only",
        evidence_refs=tuple(refs),
        reference_types=("control",),
        narrative_rule=narrative_rule,
    )


def plan():
    return ScreenPurposeGroundingPlan(
        supported_actions=(
            hint("search", ("control:search", "field:ruc")),
            hint("navigate", ("event:next",)),
            hint("view", ("table:results",)),
        ),
        forbidden_actions=("create", "edit", "delete", "process"),
    )


def draft(capability):
    return json.dumps(
        {
            "semantic_type": "screen_purpose",
            "screen_id": "screen:test",
            "supported_capabilities": [capability],
        },
        ensure_ascii=False,
    )


def parse(capability, *, grounding_plan=None):
    return parse_generation_draft(
        draft(capability),
        screen_id="screen:test",
        screen_title="Retenciones",
        grounding_plan=grounding_plan or plan(),
    )


def alternatives(schema):
    return schema["properties"]["supported_capabilities"]["items"]["oneOf"]


def test_schema_is_deterministic_and_derived_only_from_supported_actions():
    grounding_plan = plan()
    first = build_screen_purpose_generation_schema(grounding_plan, screen_id="screen:test")
    second = build_screen_purpose_generation_schema(grounding_plan, screen_id="screen:test")
    assert first == second
    assert first["additionalProperties"] is False
    assert "purpose_summary" not in first["properties"]
    assert "purpose_summary" not in first["required"]
    assert first["properties"]["screen_id"] == {"const": "screen:test"}
    assert first["properties"]["supported_capabilities"]["minItems"] == 1
    assert first["properties"]["supported_capabilities"]["maxItems"] == 3
    assert first["properties"]["supported_capabilities"]["uniqueItems"] is True
    assert set(first["required"]) == {"semantic_type", "screen_id", "supported_capabilities"}
    assert set(first["properties"]) == {"semantic_type", "screen_id", "supported_capabilities"}
    consts = {item["properties"]["action"]["const"] for item in alternatives(first)}
    assert consts == {"search", "navigate", "view"}
    assert consts.isdisjoint(grounding_plan.forbidden_actions)


def test_schema_delegates_only_action_selection_to_model():
    schema = build_screen_purpose_generation_schema(plan(), screen_id="screen:test")
    for item in alternatives(schema):
        assert item["required"] == ["action"]
        assert set(item["properties"]) == {"action"}
        assert "statement" not in item["properties"]
        assert "evidence_refs" not in item["properties"]


def test_no_supported_actions_stops_before_generation():
    empty = ScreenPurposeGroundingPlan(
        supported_actions=(),
        forbidden_actions=("search", "navigate", "view", "create", "edit", "delete", "process"),
    )
    with pytest.raises(InferenceGroundingError) as captured:
        build_screen_purpose_generation_schema(empty, screen_id="screen:test")
    assert captured.value.category == "no_supported_generation_actions"


def test_single_supported_action_caps_schema_array_at_one_item():
    single = ScreenPurposeGroundingPlan(
        supported_actions=(hint("view", ("table:results",)),),
        forbidden_actions=("search", "navigate", "create", "edit", "delete", "process"),
    )
    schema = build_screen_purpose_generation_schema(single, screen_id="screen:test")
    assert schema["properties"]["supported_capabilities"]["maxItems"] == 1


def test_removed_generation_fields_are_rejected():
    capability = {"action": "search"}
    values = json.loads(draft(capability))
    for update in (
        {"purpose_summary": "Texto generado no permitido."},
        {"limitations": ["Texto no permitido."]},
        {"uncertainties": ["Texto no permitido."]},
    ):
        with pytest.raises(InferenceSchemaError):
            parse_generation_draft(
                json.dumps({**values, **update}),
                screen_id="screen:test",
                screen_title="Retenciones",
                grounding_plan=plan(),
            )


def test_empty_capabilities_have_sanitized_domain_category():
    values = json.loads(draft({"action": "search"}))
    values["supported_capabilities"] = []
    with pytest.raises(InferenceGroundingError) as captured:
        parse_generation_draft(
            json.dumps(values),
            screen_id="screen:test",
            screen_title="Retenciones",
            grounding_plan=plan(),
        )
    assert captured.value.category == "no_supported_capabilities_generated"


@pytest.mark.parametrize(
    ("action", "canonical_statement", "expected_refs"),
    [
        (
            "search",
            "Permite buscar mediante los criterios disponibles.",
            ["control:search", "field:ruc"],
        ),
        (
            "navigate",
            "Permite navegar entre las páginas de resultados.",
            ["event:next"],
        ),
        (
            "view",
            "Permite visualizar información disponible en la pantalla.",
            ["table:results"],
        ),
    ],
)
def test_valid_single_action_drafts_are_rendered_as_controlled_public_claims(
    action, canonical_statement, expected_refs
):
    inference = parse({"action": action})
    claim = inference.supported_capabilities[0]
    assert claim.statement == canonical_statement
    assert claim.evidence_refs == expected_refs
    assert "action" not in inference.model_dump(mode="json")["supported_capabilities"][0]


def test_forbidden_action_is_not_representable_or_mappable():
    schema = build_screen_purpose_generation_schema(plan(), screen_id="screen:test")
    assert "edit" not in {item["properties"]["action"]["const"] for item in alternatives(schema)}
    with pytest.raises(InferenceUnsupportedActionError):
        parse({"action": "edit"})


def test_model_cannot_supply_statement_or_evidence_refs():
    for extra in (
        {"statement": "Permite visualizar retenciones registradas."},
        {"evidence_refs": ["table:results"]},
    ):
        with pytest.raises(InferenceSchemaError):
            parse({"action": "view", **extra})


def test_duplicate_and_reordered_actions_are_canonicalized_to_grounding_plan_order():
    raw = json.dumps(
        {
            "semantic_type": "screen_purpose",
            "screen_id": "screen:test",
            "supported_capabilities": [
                {"action": "view"},
                {"action": "search"},
                {"action": "view"},
            ],
        }
    )
    inference = parse_generation_draft(
        raw,
        screen_id="screen:test",
        screen_title="Retenciones",
        grounding_plan=plan(),
    )
    assert [claim.statement for claim in inference.supported_capabilities] == [
        "Permite buscar mediante los criterios disponibles.",
        "Permite visualizar información disponible en la pantalla.",
    ]
    assert [claim.evidence_refs for claim in inference.supported_capabilities] == [
        ["control:search", "field:ruc"],
        ["table:results"],
    ]
    assert inference.purpose_summary == "Permite buscar y consultar retenciones."


def test_prudent_only_public_wording_is_determined_by_grounding_plan():
    prudent_plan = ScreenPurposeGroundingPlan(
        supported_actions=(hint("create", ("control:new",), narrative_rule="prudent_only"),),
        forbidden_actions=("edit", "delete", "process"),
    )
    inference = parse({"action": "create"}, grounding_plan=prudent_plan)
    assert inference.supported_capabilities[0].statement == (
        "La interfaz presenta una opción relacionada con crear."
    )


def test_draft_models_are_strict_and_frozen():
    capability = GeneratedCapabilityDraft(action="search")
    with pytest.raises(ValidationError):
        capability.action = "view"
    with pytest.raises(ValidationError):
        ScreenPurposeGenerationDraft.model_validate(
            {
                "semantic_type": "screen_purpose",
                "screen_id": "screen:test",
                "supported_capabilities": [capability],
                "limitations": [],
                "uncertainties": [],
                "extra": True,
            }
        )


def capability(action, statement=None, reference=None):
    return GeneratedCapabilityDraft(action=action)


def summary(capabilities, *, grounding_plan=None):
    return build_deterministic_purpose_summary(
        screen_title="Retenciones",
        capabilities=capabilities,
        grounding_plan=grounding_plan or plan(),
    )


def test_deterministic_summary_covers_canonical_direct_combinations():
    search = capability("search", "Permite buscar retenciones.", "control:search")
    view = capability("view", "Permite visualizar retenciones.", "table:results")
    navigate = capability("navigate", "Permite navegar a la siguiente página.", "event:next")
    assert summary([search]) == "Permite buscar retenciones desde la pantalla."
    assert summary([view]) == "Permite visualizar retenciones desde la pantalla."
    assert summary([navigate]) == "Permite navegar entre las páginas de resultados de retenciones."
    assert summary([search, view]) == "Permite buscar y consultar retenciones."
    assert summary([search, view, navigate]) == (
        "Permite buscar y consultar retenciones, así como navegar entre las páginas de resultados."
    )


def test_summary_is_order_stable_deduplicated_and_does_not_infer_absent_actions():
    search = capability("search", "Permite buscar retenciones.", "control:search")
    view = capability("view", "Permite visualizar retenciones.", "table:results")
    assert summary([view, search, search]) == summary([search, view])
    result = summary([search])
    assert "editar" not in result and "eliminar" not in result
    assert "gestionar" not in result and "administrar" not in result


def test_summary_distinguishes_direct_and_prudent_mutative_support():
    direct_plan = ScreenPurposeGroundingPlan(
        supported_actions=(hint("create", ("control:new",)),),
        forbidden_actions=("edit", "delete", "process"),
    )
    prudent_plan = ScreenPurposeGroundingPlan(
        supported_actions=(hint("create", ("control:new",), narrative_rule="prudent_only"),),
        forbidden_actions=("edit", "delete", "process"),
    )
    create = capability("create", "Permite crear una retención nueva.", "control:new")
    assert summary([create], grounding_plan=direct_plan) == (
        "Permite crear retenciones desde la pantalla."
    )
    assert summary([create], grounding_plan=prudent_plan) == (
        "La pantalla Retenciones presenta una opción relacionada con crear."
    )
