from copy import deepcopy

from erp_assistant.structural.canonical.builder import CanonicalKnowledgeBuilder
from erp_assistant.structural.canonical.ids import normalize_text, stable_id
from tests.fixtures.canonical import fictional_artifacts, fictional_profile


def _product_control(artifacts):
    kb = CanonicalKnowledgeBuilder().build(fictional_profile(), artifacts)
    screen = next(item for item in kb.screens if item.route == "/app/inventory/products")
    controls = [item for item in kb.controls if item.screen_id == screen.id]
    assert len(controls) == 1
    return kb, screen, controls[0]


def test_icon_label_enrichment_preserves_pre_icon_control_identity():
    before = fictional_artifacts()
    product_before = before["screen_index.json"]["screens"][1]
    product_before["buttons"] = [
        {
            "text": "",
            "region": "main_content",
        }
    ]

    after = deepcopy(before)
    product_after = after["screen_index.json"]["screens"][1]
    product_after["buttons"][0].update(
        {
            "icon_label": "edit",
            "icon_source": "svgIcon",
        }
    )

    before_kb, before_screen, before_control = _product_control(before)
    after_kb, after_screen, after_control = _product_control(after)

    assert before_kb.generator_version == "4.0.6"
    assert after_kb.generator_version == "4.0.6"
    assert before_screen.id == after_screen.id

    assert before_control.label == "unlabeled control"
    assert after_control.label == "edit"
    assert after_control.normalized_label == "edit"

    expected_id = stable_id(
        "control",
        before_screen.id,
        "button",
        normalize_text("unlabeled control"),
        0,
    )
    assert before_control.id == expected_id
    assert after_control.id == expected_id


def test_changing_only_icon_label_changes_display_semantics_not_identity():
    edit = fictional_artifacts()
    edit["screen_index.json"]["screens"][1]["buttons"] = [
        {
            "text": "",
            "icon_label": "edit",
            "icon_source": "svgIcon",
            "region": "main_content",
        }
    ]

    delete = deepcopy(edit)
    delete["screen_index.json"]["screens"][1]["buttons"][0]["icon_label"] = "delete"

    _, _, edit_control = _product_control(edit)
    _, _, delete_control = _product_control(delete)

    assert edit_control.label == "edit"
    assert delete_control.label == "delete"
    assert edit_control.id == delete_control.id


def test_explicit_accessible_label_remains_part_of_control_identity():
    artifacts = fictional_artifacts()
    artifacts["screen_index.json"]["screens"][1]["buttons"] = [
        {
            "aria_label": "Editar producto",
            "icon_label": "edit",
            "icon_source": "svgIcon",
            "region": "main_content",
        }
    ]

    _, screen, control = _product_control(artifacts)

    assert control.label == "Editar producto"
    assert control.id == stable_id(
        "control",
        screen.id,
        "button",
        normalize_text("Editar producto"),
        0,
    )
