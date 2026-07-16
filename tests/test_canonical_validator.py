from copy import deepcopy

from src.knowledge.canonical.models import CanonicalKnowledgeBase
from src.knowledge.canonical.validator import CanonicalKnowledgeValidator
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
