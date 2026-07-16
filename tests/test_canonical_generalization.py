from src.knowledge.canonical.builder import CanonicalKnowledgeBuilder
from src.knowledge.canonical.validator import CanonicalKnowledgeValidator
from tests.canonical_fixtures import fictional_artifacts, fictional_profile


def test_builder_is_framework_and_route_prefix_independent():
    kb=CanonicalKnowledgeBuilder().build(fictional_profile(), fictional_artifacts())
    serialized=kb.model_dump_json().casefold()
    assert "/app/inventory/products" in serialized
    assert all(term not in serialized for term in ("/admin", "angular", "fuse", "cbmm", "cuentas por cobrar"))
    assert not CanonicalKnowledgeValidator().errors(kb)
