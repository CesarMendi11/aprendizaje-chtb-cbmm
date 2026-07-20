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


def test_main_content_is_only_deduplicated_structural_text():
    artifacts = fictional_artifacts()
    artifacts["screen_index.json"]["screens"][1].update({
        "main_visible_text": (
            "Persona Ficticia 1799999999001 001-001-000000001 "
            "31 dic 2025 $1,234.56 Total de registros: 47"
        ),
        "inputs": [
            {"label": "RUC", "name": "tax_id", "value": "1799999999001"},
            {"label": "RUC", "placeholder": "Buscar"},
        ],
        "buttons": [{"text": "Buscar"}],
        "tables": [{
            "name": "Resultados",
            "headers": ["Fecha de emisión", "Número de factura", "Total retenido"],
            "rows": [["Persona Ficticia", "001-001-000000001", "$1,234.56"]],
            "row_count_observed": 47,
        }],
    })
    first = CanonicalKnowledgeBuilder().build(fictional_profile(), artifacts)
    second = CanonicalKnowledgeBuilder().build(fictional_profile(), artifacts)
    screen = next(item for item in first.screens if item.route == "/app/inventory/products")
    assert screen.main_content_text == (
        "Products | RUC | Buscar | Resultados | Fecha de emisión | "
        "Número de factura | Total retenido | Suppliers"
    )
    assert screen.main_content_text == next(
        item.main_content_text for item in second.screens if item.route == screen.route
    )
    assert "Persona Ficticia" not in screen.main_content_text
    assert "Total de registros" not in screen.main_content_text
    assert "47" not in screen.main_content_text
    assert screen.main_content_text.count("RUC") == 1
    assert screen.main_content_text.count("Buscar") == 1


def test_build_report_counts_excluded_dynamic_sources_without_values():
    builder = CanonicalKnowledgeBuilder()
    kb = builder.build(fictional_profile(), fictional_artifacts())
    report = builder.build_report(kb)
    assert report["sensitive_regions_excluded"] == 2
    assert report["omitted_entities"]["dynamic_text_sources"] == 1
    assert "owner@example.test" not in json.dumps(report)


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
