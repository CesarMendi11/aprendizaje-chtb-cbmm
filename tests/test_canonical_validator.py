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
