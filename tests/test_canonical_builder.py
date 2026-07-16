import json

import pytest

from src.knowledge.canonical.builder import ArtifactLoadError, CanonicalKnowledgeBuilder
from src.knowledge.canonical.exporter import CanonicalKnowledgeExporter
from src.knowledge.canonical.ids import content_hash
from src.knowledge.canonical.repository import CanonicalKnowledgeRepository
from tests.canonical_fixtures import fictional_artifacts, fictional_profile


def build(): return CanonicalKnowledgeBuilder().build(fictional_profile(), fictional_artifacts())


def test_builder_preserves_typed_elements_and_transitions():
    kb = build()
    product = next(item for item in kb.screens if item.route == "/app/inventory/products")
    assert product.module_id
    assert [item.label for item in kb.fields if item.screen_id == product.id] == ["SKU"]
    assert [item.label for item in kb.controls if item.screen_id == product.id] == ["Search"]
    assert len(kb.tables) == 1 and len(kb.table_columns) == 2
    assert len(kb.transitions) == 1 and kb.transitions[0].route_changed


def test_privacy_removes_sensitive_and_volatile_content():
    kb = build()
    home = next(item for item in kb.screens if item.route == "/app/home")
    assert "10.1.2.3" not in home.main_content_text
    assert "owner@example.test" not in home.main_content_text
    assert all(item.label != "Secret" for item in kb.fields)


def test_knowledge_version_is_deterministic():
    assert build().knowledge_version == build().knowledge_version


def test_export_manifest_hashes_and_repository(tmp_path):
    builder = CanonicalKnowledgeBuilder(); kb = builder.build(fictional_profile(), fictional_artifacts())
    CanonicalKnowledgeExporter().export(kb, tmp_path, build_report=builder.build_report(kb))
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["knowledge_version"] == kb.knowledge_version
    assert len(manifest["canonical_document_hash"]) == 64
    repo = CanonicalKnowledgeRepository(tmp_path / "knowledge.json")
    screen = repo.get_screen_by_route("/app/inventory/products?x=1")
    assert screen.title == "Products"
    assert repo.get_fields(screen.id)[0].label == "SKU"
    assert repo.get_controls(screen.id)[0].control_type == "button"
    assert repo.get_transitions()


def test_missing_and_corrupt_artifacts(tmp_path):
    builder=CanonicalKnowledgeBuilder(tmp_path)
    (tmp_path/"profile.yaml").write_text("erp:\n  name: Test\n  code: test\noutput: {}\n", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="ausente"):
        builder.build_from_paths("profile.yaml", "missing")
    (tmp_path/"structural").mkdir()
    (tmp_path/"structural"/"screen_index.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="corrupto"):
        builder.build_from_paths("profile.yaml", "structural")
    (tmp_path/"profile.yaml").write_text("erp: [", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="Perfil inválido"):
        builder.build_from_paths("profile.yaml")
