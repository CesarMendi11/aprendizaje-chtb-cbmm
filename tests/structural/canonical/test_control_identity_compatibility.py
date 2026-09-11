from copy import deepcopy

from erp_assistant.structural.canonical.builder import CanonicalKnowledgeBuilder
from erp_assistant.structural.canonical.ids import normalize_text, stable_id
from tests.fixtures.canonical import fictional_artifacts, fictional_profile


def _product_controls(artifacts):
    kb = CanonicalKnowledgeBuilder().build(fictional_profile(), artifacts)
    screen = next(item for item in kb.screens if item.route == "/app/inventory/products")
    controls = [item for item in kb.controls if item.screen_id == screen.id]
    return kb, screen, controls


def test_unlabeled_control_is_not_promoted_until_functional_label_is_observed():
    before = fictional_artifacts()
    before["screen_index.json"]["screens"][1]["buttons"] = [
        {"text": "", "region": "main_content"}
    ]

    after = deepcopy(before)
    after["screen_index.json"]["screens"][1]["buttons"][0].update(
        {"icon_label": "edit", "icon_source": "svgIcon"}
    )

    before_kb, _, before_controls = _product_controls(before)
    after_kb, screen, after_controls = _product_controls(after)

    assert before_kb.generator_version == "4.1.0"
    assert after_kb.generator_version == "4.1.0"
    assert before_controls == []
    assert len(after_controls) == 1
    assert after_controls[0].label == "edit"
    assert after_controls[0].id == stable_id(
        "control",
        screen.id,
        "button",
        normalize_text("edit"),
        "main_content",
    )


def test_icon_label_is_part_of_functional_control_identity():
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

    _, _, edit_controls = _product_controls(edit)
    _, _, delete_controls = _product_controls(delete)

    assert edit_controls[0].label == "edit"
    assert delete_controls[0].label == "delete"
    assert edit_controls[0].id != delete_controls[0].id


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

    _, screen, controls = _product_controls(artifacts)
    control = controls[0]

    assert control.label == "Editar producto"
    assert control.id == stable_id(
        "control",
        screen.id,
        "button",
        normalize_text("Editar producto"),
        "main_content",
    )


def test_repeated_row_controls_collapse_to_one_functional_control():
    artifacts = fictional_artifacts()
    artifacts["screen_index.json"]["screens"][1]["buttons"] = [
        {
            "text": "",
            "icon_label": "edit",
            "region": "main_content",
            "within_table": True,
        }
        for _ in range(12)
    ]

    kb, _, controls = _product_controls(artifacts)

    assert [control.label for control in controls] == ["edit"]
    assert kb.generator_version == "4.1.0"


def test_adding_another_repeated_row_does_not_change_existing_control_identity():
    before = fictional_artifacts()
    before["screen_index.json"]["screens"][1]["buttons"] = [
        {"icon_label": "mail", "region": "main_content", "within_table": True}
        for _ in range(3)
    ] + [
        {"aria_label": "Siguiente página", "region": "main_content"}
    ]
    after = deepcopy(before)
    after["screen_index.json"]["screens"][1]["buttons"].insert(
        3,
        {"icon_label": "mail", "region": "main_content", "within_table": True},
    )

    _, _, before_controls = _product_controls(before)
    _, _, after_controls = _product_controls(after)

    before_ids = {control.label: control.id for control in before_controls}
    after_ids = {control.label: control.id for control in after_controls}
    assert before_ids == after_ids
