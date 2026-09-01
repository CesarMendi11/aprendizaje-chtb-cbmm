from erp_assistant.structural.canonical.privacy import sanitize_artifact_payload


def test_persisted_artifact_drops_rendered_text_and_query_values():
    payload = {
        "url": "https://erp.example.test/admin/personas?email=alice@example.test&view=1",
        "path": "/admin/personas?document=0701234567",
        "visible_text": "Persona Alice alice@example.test 0701234567 $125.50",
        "main_visible_text": "Fila real que no debe persistirse",
        "regions": {
            "main_content": {
                "visible_text": "Alice alice@example.test",
                "elements_count": 4,
            }
        },
    }

    safe = sanitize_artifact_payload(payload)

    assert "visible_text" not in safe
    assert "main_visible_text" not in safe
    assert "visible_text" not in safe["regions"]["main_content"]
    assert safe["regions"]["main_content"]["elements_count"] == 4
    assert "alice@example.test" not in safe["url"]
    assert "0701234567" not in safe["path"]
    assert "email=%2A" in safe["url"]
    assert "document=%2A" in safe["path"]


def test_persisted_artifact_preserves_structural_labels_but_drops_sensitive_fragments():
    payload = {
        "buttons": [
            {"text": "Buscar", "region": "main_content", "within_table": False},
            {
                "text": "alice@example.test",
                "region": "main_content",
                "within_table": False,
            },
            {
                "text": "Usuario Alice",
                "region": "header",
                "within_table": False,
            },
            {
                "text": "Editar Alice",
                "region": "main_content",
                "within_table": True,
                "selector": "table > tr:nth-of-type(2) > button",
            },
        ],
        "tables": [
            {
                "headers": ["RUC", "Razón social", "Monto"],
                "rows_count": 10,
                "region": "main_content",
                "within_table": True,
            }
        ],
    }

    safe = sanitize_artifact_payload(payload)

    assert safe["buttons"][0]["text"] == "Buscar"
    assert safe["buttons"][1]["text"] == ""
    assert "text" not in safe["buttons"][2]
    assert "text" not in safe["buttons"][3]
    assert "selector" not in safe["buttons"][3]
    assert safe["tables"][0]["headers"] == ["RUC", "Razón social", "Monto"]
    assert safe["tables"][0]["rows_count"] == 10


def test_persisted_artifact_keeps_hashes_and_safe_navigation_selectors():
    digest = "a" * 64
    selector = "fuse-vertical-navigation > fuse-vertical-navigation-basic-item:nth-of-type(2)"
    payload = {
        "structural_signature": digest,
        "observed_exact_signatures": [digest],
        "selector": selector,
    }

    safe = sanitize_artifact_payload(payload)

    assert safe["structural_signature"] == digest
    assert safe["observed_exact_signatures"] == [digest]
    assert safe["selector"] == selector


def test_persisted_candidate_uses_nested_region_metadata_for_redaction():
    payload = {
        "candidate": {
            "label": "Usuario Alice",
            "selector": "header > button",
            "metadata": {
                "region": "header",
                "within_table": False,
            },
        }
    }

    safe = sanitize_artifact_payload(payload)

    assert "label" not in safe["candidate"]
    assert safe["candidate"]["selector"] == "header > button"


def test_persisted_artifact_drops_document_title():
    safe = sanitize_artifact_payload(
        {
            "document_title": "Alice - Persona",
            "functional_title": "Personas",
        }
    )

    assert "document_title" not in safe
    assert safe["functional_title"] == "Personas"


def test_persisted_route_keeps_only_internal_state_fragment():
    safe = sanitize_artifact_payload(
        {
            "route": "/admin/home#state:abcdef123456",
            "target_route": "/admin/home#alice@example.test",
        }
    )

    assert safe["route"] == "/admin/home#state:abcdef123456"
    assert safe["target_route"] == "/admin/home"
