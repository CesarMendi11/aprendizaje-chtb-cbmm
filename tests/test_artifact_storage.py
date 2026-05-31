import json

from src.storage.artifact_storage import ArtifactStorage, safe_slug


def build_profile(tmp_path):
    return {
        "output": {
            "raw_playwright_dir": str(tmp_path / "data/raw/playwright"),
            "html_dir": str(tmp_path / "data/raw/html"),
            "screenshots_dir": str(tmp_path / "data/raw/screenshots"),
            "marked_screenshots_dir": str(tmp_path / "data/raw/marked_screenshots"),
            "processed_structural_dir": str(tmp_path / "data/processed/structural"),
            "processed_semantic_dir": str(tmp_path / "data/processed/semantic"),
            "review_structural_dir": str(tmp_path / "data/review/structural"),
            "review_semantic_dir": str(tmp_path / "data/review/semantic"),
            "approved_neo4j_dir": str(tmp_path / "data/approved/neo4j"),
            "approved_chromadb_dir": str(tmp_path / "data/approved/chromadb"),
            "rejected_dir": str(tmp_path / "data/rejected"),
            "cache_dir": str(tmp_path / "data/cache"),
        }
    }


def test_safe_slug_converts_routes_to_valid_filename():
    assert safe_slug("/admin/Cuentas por cobrar/Facturas") == (
        "admin_cuentas_por_cobrar_facturas"
    )


def test_artifact_storage_creates_directories(tmp_path):
    storage = ArtifactStorage(build_profile(tmp_path))

    assert storage.raw_playwright_dir.exists()
    assert storage.html_dir.exists()
    assert storage.screenshots_dir.exists()
    assert storage.processed_structural_dir.exists()
    assert storage.review_structural_dir.exists()


def test_artifact_storage_saves_raw_json(tmp_path):
    storage = ArtifactStorage(build_profile(tmp_path))

    path = storage.save_raw_screen_json(
        {"route": "/admin/home", "status": "discovered"},
        "/admin/home",
    )

    assert path.exists()

    with path.open("r", encoding="utf-8") as file:
        content = json.load(file)

    assert content["route"] == "/admin/home"
    assert content["status"] == "discovered"


def test_artifact_storage_saves_html_content(tmp_path):
    storage = ArtifactStorage(build_profile(tmp_path))

    path = storage.save_html_content("<html><body>ERP</body></html>", "/admin/home")

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "<html><body>ERP</body></html>"


def test_artifact_storage_saves_uncertainty_json(tmp_path):
    storage = ArtifactStorage(build_profile(tmp_path))

    path = storage.save_uncertainty_json(
        {
            "route": "/admin/facturas",
            "reason": "Pantalla con elementos dinamicos",
        },
        "/admin/facturas",
    )

    assert path.exists()
    assert path.name.endswith("_uncertainty.json")