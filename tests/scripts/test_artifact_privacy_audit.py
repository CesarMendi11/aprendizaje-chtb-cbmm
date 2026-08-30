import json
from pathlib import Path

from scripts.audit.audit_artifact_privacy import audit_tree


def test_privacy_audit_passes_safe_structural_tree(tmp_path: Path):
    root = tmp_path / "run"
    path = root / "processed" / "structural" / "screen_index.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "screens": [
                    {
                        "route": "/admin/personas?view=*#state:abcdef123456",
                        "functional_title": "Personas",
                        "buttons": [{"text": "Buscar"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = audit_tree(root)

    assert report["status"] == "passed"
    assert report["violation_count"] == 0


def test_privacy_audit_fails_without_echoing_sensitive_value(tmp_path: Path):
    root = tmp_path / "run"
    path = root / "raw" / "playwright" / "screen.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "visible_text": "Alice alice@example.test",
                "route": "/admin/personas?id=0701234567#alice@example.test",
            }
        ),
        encoding="utf-8",
    )
    (root / "raw" / "screenshots").mkdir(parents=True)
    (root / "raw" / "screenshots" / "screen.png").write_bytes(b"png")

    report = audit_tree(root)
    rendered = json.dumps(report, ensure_ascii=False)

    assert report["status"] == "failed"
    assert report["violation_count"] >= 3
    assert "alice@example.test" not in rendered
    assert "0701234567" not in rendered
