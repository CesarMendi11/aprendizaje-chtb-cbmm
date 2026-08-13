from copy import deepcopy

from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.validator import CanonicalKnowledgeValidator
from src.database.services.payloads import validate_safe_json
from tests.test_canonical_builder import build


def codes(payload):
    kb=CanonicalKnowledgeBase.model_validate(payload)
    return {item.code for item in CanonicalKnowledgeValidator().validate(kb)}


def test_detects_orphan_reference():
    payload=build().model_dump(mode="json"); payload["fields"][0]["screen_id"]="screen:missing"
    assert "unresolved_reference" in codes(payload)


def test_detects_duplicate_ids_and_routes():
    payload=build().model_dump(mode="json")
    duplicate=deepcopy(payload["screens"][0]); duplicate["id"]="screen:other"; payload["screens"].append(duplicate)
    assert "duplicate_route" in codes(payload)
    payload["screens"][-1]["route"]="/app/other"; payload["screens"][-1]["id"]=payload["screens"][0]["id"]
    assert "duplicate_id" in codes(payload)


def test_structural_labels_are_not_sensitive():
    payload=build().model_dump(mode="json")
    payload["screens"][0]["main_content_text"] = (
        "RUC | Fecha de emisión | Número de factura | Total retenido"
    )
    assert "sensitive_content" not in codes(payload)


def test_screen_and_evidence_reject_concrete_sensitive_values():
    samples = [
        "1799999999001",
        "001-001-000000001",
        "$1,234.56",
        "31 dic 2025",
        "persona@example.test",
        "192.0.2.44",
        "token=abcdefghijklmnopqrstuvwxyz1234567890",
    ]
    for sample in samples:
        payload=build().model_dump(mode="json")
        payload["screens"][0]["main_content_text"] = sample
        assert "sensitive_content" in codes(payload)
        payload=build().model_dump(mode="json")
        payload["evidence"][0]["observed_text"] = sample
        assert "sensitive_content" in codes(payload)


def test_safe_json_rejects_concrete_identifiers_and_transactions():
    for sample in ("1799999999001", "001-001-000000001", "$1,234.56", "31 dic 2025"):
        try:
            validate_safe_json({"description": sample})
        except ValueError:
            pass
        else:
            raise AssertionError(f"El valor sintético no fue rechazado: {sample!r}")


def test_module_parent_reference_is_validated():
    payload = build().model_dump(mode="json")
    payload["modules"][0]["parent_module_id"] = "module:missing"

    assert "unresolved_reference" in codes(payload)


def test_module_hierarchy_detects_self_parent():
    payload = build().model_dump(mode="json")
    module = payload["modules"][0]

    module["parent_module_id"] = module["id"]

    assert "module_self_parent" in codes(payload)


def test_module_hierarchy_detects_cycles():
    payload = build().model_dump(mode="json")
    first, second = payload["modules"][:2]

    first["parent_module_id"] = second["id"]
    first["depth"] = 1
    first["navigation_path"] = [second["name"], first["name"]]

    second["parent_module_id"] = first["id"]
    second["depth"] = 1
    second["navigation_path"] = [first["name"], second["name"]]

    assert "module_cycle" in codes(payload)


def test_module_hierarchy_validates_depth_and_navigation_path():
    payload = build().model_dump(mode="json")
    first, second = payload["modules"][:2]

    second["parent_module_id"] = first["id"]
    second["depth"] = 7
    second["navigation_path"] = [second["name"]]

    result = codes(payload)

    assert "module_depth_mismatch" in result
    assert "module_navigation_path_mismatch" in result


def test_schema_v1_is_rejected_after_vnext_cutover():
    payload = build().model_dump(mode="json")
    payload["schema_version"] = "1.0.0"
    knowledge = CanonicalKnowledgeBase.model_validate(payload)

    issues = CanonicalKnowledgeValidator().validate(knowledge)

    assert any(item.code == "unsupported_schema" for item in issues)

def test_safe_json_does_not_treat_numeric_runs_inside_canonical_ids_as_sensitive():
    payload = {
        "screen_id": "screen:8835443310af",
        "module_id": "module:1234567890ab",
        # A stable hash can be numeric-only by chance; it is still an ID.
        "event_id": "event:344365453141",
        "transition_id": "transition:test:1234567890123",
    }

    assert validate_safe_json(payload) == payload


def test_safe_json_still_rejects_standalone_numeric_business_identifiers():
    for value in (
        "1234567890",
        "1799999999001",
        "RUC: 1799999999001",
    ):
        try:
            validate_safe_json({"value": value})
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"El identificador concreto debía rechazarse: {value!r}"
            )


def test_safe_json_accepts_generated_structural_navigation_selector():
    selector = (
        "app-root > layout > admin-layout > fuse-vertical-navigation > div > "
        "div:nth-of-type(2) > fuse-vertical-navigation-group-item > "
        "fuse-vertical-navigation-collapsable-item:nth-of-type(10)"
    )

    payload = {
        "metadata": {
            "navigation_origin": selector,
            "navigation_origin_path": selector,
        }
    }

    assert validate_safe_json(payload) == payload


def test_safe_json_does_not_whitelist_plain_long_secret_as_navigation_origin():
    payload = {
        "metadata": {
            "navigation_origin": "abcdefghijklmnopqrstuvwxyz1234567890",
            "navigation_origin_path": "abcdefghijklmnopqrstuvwxyz1234567890",
        }
    }

    try:
        validate_safe_json(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("Un token largo no debe aceptarse como selector estructural")


def test_safe_json_rejects_long_secret_embedded_in_selector_shaped_value():
    payload = {
        "metadata": {
            "navigation_origin": "div > abcdefghijklmnopqrstuvwxyz1234567890",
            "navigation_origin_path": "div > abcdefghijklmnopqrstuvwxyz1234567890",
        }
    }

    try:
        validate_safe_json(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("Un token opaco no debe disfrazarse de selector CSS")
