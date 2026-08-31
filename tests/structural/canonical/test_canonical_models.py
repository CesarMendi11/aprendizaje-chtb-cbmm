import pytest
from pydantic import ValidationError

from erp_assistant.structural.canonical.enums import ReviewStatus
from erp_assistant.structural.canonical.models import ERPSystem, Module


def test_generated_entity_is_pending_review():
    module = Module(
        id="module:1",
        erp_id="erp:1",
        parent_module_id=None,
        depth=0,
        navigation_path=["Inventory"],
        name="Inventory",
        normalized_name="inventory",
    )
    assert module.review_status is ReviewStatus.PENDING_REVIEW
    assert module.reviewed_at is None


def test_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        ERPSystem(id="erp:1", slug="erp", name="ERP", profile_name="test", password="bad")


def test_module_hierarchy_contract_is_required():
    with pytest.raises(ValidationError):
        Module(
            id="module:1",
            erp_id="erp:1",
            name="Inventory",
            normalized_name="inventory",
        )
