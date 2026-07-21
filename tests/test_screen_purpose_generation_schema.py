from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.analysis.generation.errors import (
    InferenceGroundingError,
    InferenceReferenceError,
    InferenceSchemaError,
    InferenceUnsupportedActionError,
)
from src.analysis.generation.screen_purpose_generation import (
    GeneratedCapabilityDraft,
    ScreenPurposeGenerationDraft,
    build_deterministic_purpose_summary,
    build_screen_purpose_generation_schema,
    parse_generation_draft,
)
from src.analysis.schemas import ActionGroundingHint, ScreenPurposeGroundingPlan


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
            "limitations": [],
            "uncertainties": [],
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
    first = build_screen_purpose_generation_schema(
        grounding_plan, screen_id="screen:test"
    )
    second = build_screen_purpose_generation_schema(
        grounding_plan, screen_id="screen:test"
    )
    assert first == second
    assert first["additionalProperties"] is False
    assert "purpose_summary" not in first["properties"]
    assert "purpose_summary" not in first["required"]
    assert first["properties"]["screen_id"] == {"const": "screen:test"}
    assert first["properties"]["supported_capabilities"]["minItems"] == 1
    assert first["properties"]["limitations"] == {"type": "array", "maxItems": 0}
    assert first["properties"]["uncertainties"] == {"type": "array", "maxItems": 0}
    consts = {item["properties"]["action"]["const"] for item in alternatives(first)}
    assert consts == {"search", "navigate", "view"}
    assert consts.isdisjoint(grounding_plan.forbidden_actions)


def test_each_schema_action_has_only_its_own_references():
    schema = build_screen_purpose_generation_schema(plan(), screen_id="screen:test")
    references = {
        item["properties"]["action"]["const"]: set(
            item["properties"]["evidence_refs"]["items"]["enum"]
        )
        for item in alternatives(schema)
    }
    assert references == {
        "search": {"control:search", "field:ruc"},
        "navigate": {"event:next"},
        "view": {"table:results"},
    }


def test_no_supported_actions_stops_before_generation():
    empty = ScreenPurposeGroundingPlan(
        supported_actions=(),
        forbidden_actions=("search", "navigate", "view", "create", "edit", "delete", "process"),
    )
    with pytest.raises(InferenceGroundingError) as captured:
        build_screen_purpose_generation_schema(empty, screen_id="screen:test")
    assert captured.value.category == "no_supported_generation_actions"


def test_removed_summary_and_nonempty_negative_lists_are_rejected():
    capability = {
        "action": "search",
        "statement": "Permite buscar retenciones registradas.",
        "evidence_refs": ["control:search"],
    }
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
    values = json.loads(
        draft(
            {
                "action": "search",
                "statement": "Permite buscar retenciones registradas.",
                "evidence_refs": ["control:search"],
            }
        )
    )
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
    ("action", "statement", "reference"),
    [
        ("search", "Permite buscar retenciones registradas.", "control:search"),
        ("navigate", "Permite navegar a la siguiente página.", "event:next"),
        ("view", "Permite visualizar retenciones registradas.", "table:results"),
    ],
)
def test_valid_single_action_drafts_map_to_public_inference(action, statement, reference):
    inference = parse(
        {"action": action, "statement": statement, "evidence_refs": [reference]}
    )
    claim = inference.supported_capabilities[0]
    assert claim.statement == statement
    assert claim.evidence_refs == [reference]
    assert "action" not in inference.model_dump(mode="json")["supported_capabilities"][0]


def test_forbidden_action_is_not_representable_or_mappable():
    schema = build_screen_purpose_generation_schema(plan(), screen_id="screen:test")
    assert "edit" not in {
        item["properties"]["action"]["const"] for item in alternatives(schema)
    }
    with pytest.raises(InferenceUnsupportedActionError):
        parse(
            {
                "action": "edit",
                "statement": "Permite editar retenciones registradas.",
                "evidence_refs": ["table:results"],
            }
        )


def test_declared_action_must_match_statement_without_leaking_it():
    statement = "Permite editar la retención registrada."
    with pytest.raises(InferenceUnsupportedActionError) as captured:
        parse(
            {
                "action": "search",
                "statement": statement,
                "evidence_refs": ["control:search"],
            }
        )
    assert captured.value.category == "declared_action_statement_mismatch"
    assert statement not in str(captured.value)


def test_action_rejects_references_from_another_hint():
    with pytest.raises(InferenceReferenceError) as captured:
        parse(
            {
                "action": "view",
                "statement": "Permite visualizar retenciones registradas.",
                "evidence_refs": ["control:search"],
            }
        )
    assert captured.value.category == "declared_action_reference_not_permitted"


def test_statement_cannot_mix_two_recognized_actions():
    with pytest.raises(InferenceUnsupportedActionError) as captured:
        parse(
            {
                "action": "search",
                "statement": "Permite buscar y visualizar retenciones.",
                "evidence_refs": ["control:search"],
            }
        )
    assert captured.value.category == "declared_action_statement_mismatch"


def test_prudent_only_requires_prudent_language_but_direct_allows_direct_language():
    prudent_plan = ScreenPurposeGroundingPlan(
        supported_actions=(hint("create", ("control:new",), narrative_rule="prudent_only"),),
        forbidden_actions=("edit", "delete", "process"),
    )
    with pytest.raises(InferenceUnsupportedActionError) as captured:
        parse(
            {
                "action": "create",
                "statement": "Permite crear una retención nueva.",
                "evidence_refs": ["control:new"],
            },
            grounding_plan=prudent_plan,
        )
    assert captured.value.category == "declared_action_requires_prudent_wording"
    assert parse(
        {
            "action": "create",
            "statement": "La interfaz presenta una opción para crear retenciones.",
            "evidence_refs": ["control:new"],
        },
        grounding_plan=prudent_plan,
    )
    assert parse(
        {
            "action": "search",
            "statement": "Permite buscar retenciones registradas.",
            "evidence_refs": ["control:search"],
        }
    )


def test_draft_models_are_strict_and_frozen():
    capability = GeneratedCapabilityDraft(
        action="search",
        statement="Permite buscar retenciones registradas.",
        evidence_refs=["control:search"],
    )
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


def capability(action, statement, reference):
    return GeneratedCapabilityDraft(
        action=action,
        statement=statement,
        evidence_refs=[reference],
    )


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
        "Permite buscar y consultar retenciones, así como navegar entre las páginas "
        "de resultados."
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
