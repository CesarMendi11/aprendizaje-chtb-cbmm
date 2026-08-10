from src.api import admin_system_service


def test_system_status_is_ok_when_all_dependencies_are_available(monkeypatch):
    monkeypatch.setattr(
        admin_system_service,
        "probe_postgresql",
        lambda _: (
            {"status": "online"},
            {
                "active_version": "version-test",
                "total_items": 21,
                "approved": 21,
                "corrected": 0,
                "pending_review": 0,
                "rejected": 0,
                "items_by_status": {"approved": 21},
                "latest_import": None,
                "sync_jobs": [],
            },
        ),
    )
    monkeypatch.setattr(
        admin_system_service,
        "probe_neo4j",
        lambda: {"status": "online", "nodes": 21, "relationships": 20},
    )
    monkeypatch.setattr(
        admin_system_service,
        "probe_chroma",
        lambda: {"status": "ready", "documents": 19},
    )
    monkeypatch.setattr(
        admin_system_service,
        "probe_ollama",
        lambda: {"status": "online"},
    )

    result = admin_system_service.collect_admin_system_status(object())

    assert result["ok"] is True
    assert result["knowledge"]["active_version"] == "version-test"
    assert result["services"]["neo4j"]["nodes"] == 21
    assert result["services"]["chroma"]["documents"] == 19


def test_system_status_keeps_partial_results_when_one_dependency_is_offline(
    monkeypatch,
):
    monkeypatch.setattr(
        admin_system_service,
        "probe_postgresql",
        lambda _: (
            {"status": "online"},
            {
                "active_version": "version-test",
                "total_items": 0,
                "approved": 0,
                "corrected": 0,
                "pending_review": 0,
                "rejected": 0,
                "items_by_status": {},
                "latest_import": None,
                "sync_jobs": [],
            },
        ),
    )
    monkeypatch.setattr(
        admin_system_service,
        "probe_neo4j",
        lambda: {"status": "offline"},
    )
    monkeypatch.setattr(
        admin_system_service,
        "probe_chroma",
        lambda: {"status": "ready"},
    )
    monkeypatch.setattr(
        admin_system_service,
        "probe_ollama",
        lambda: {"status": "online"},
    )

    result = admin_system_service.collect_admin_system_status(object())

    assert result["ok"] is False
    assert result["services"]["postgresql"]["status"] == "online"
    assert result["services"]["neo4j"]["status"] == "offline"
    assert result["services"]["chroma"]["status"] == "ready"
    assert result["services"]["ollama"]["status"] == "online"
