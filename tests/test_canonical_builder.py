import json

import pytest

from src.knowledge.canonical.builder import ArtifactLoadError, CanonicalKnowledgeBuilder
from src.knowledge.canonical.exporter import CanonicalKnowledgeExporter
from src.knowledge.canonical.repository import CanonicalKnowledgeRepository
from tests.canonical_fixtures import fictional_artifacts, fictional_profile


def build():
    return CanonicalKnowledgeBuilder().build(
        fictional_profile(),
        fictional_artifacts(),
    )


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


def test_sensitive_event_label_falls_back_to_structural_category():
    artifacts = fictional_artifacts()
    transition = artifacts["state_flow_graph.json"]["transitions"][0]
    transition["event"].update(
        {
            "event_type": "open_dropdown",
            "label": "01/08/2026 - 31/08/2026",
            "decision": "allow",
        }
    )

    builder = CanonicalKnowledgeBuilder()
    kb = builder.build(fictional_profile(), artifacts)

    assert len(kb.events) == 1
    assert kb.events[0].label == "open_dropdown"
    assert kb.events[0].normalized_label == "open dropdown"
    assert any(
        warning.code == "sensitive_event_label_replaced"
        for warning in kb.build_warnings
    )
    assert builder.sensitive_exclusions >= 2


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
    builder = CanonicalKnowledgeBuilder()
    kb = builder.build(fictional_profile(), fictional_artifacts())
    CanonicalKnowledgeExporter().export(
        kb,
        tmp_path,
        build_report=builder.build_report(kb),
    )
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["knowledge_version"] == kb.knowledge_version
    assert len(manifest["canonical_document_hash"]) == 64
    assert manifest["snapshot"] == {
        "mode": "full",
        "scope": "full",
        "target": None,
        "target_module_id": None,
        "target_screen_id": None,
        "base_knowledge_version_id": None,
        "base_knowledge_version": None,
        "erp_id": None,
    }
    repo = CanonicalKnowledgeRepository(tmp_path / "knowledge.json")
    screen = repo.get_screen_by_route("/app/inventory/products?x=1")
    assert screen.title == "Products"
    assert repo.get_fields(screen.id)[0].label == "SKU"
    assert repo.get_controls(screen.id)[0].control_type == "button"
    assert repo.get_transitions()


def test_missing_and_corrupt_artifacts(tmp_path):
    builder = CanonicalKnowledgeBuilder(tmp_path)
    (tmp_path / "profile.yaml").write_text(
        "erp:\n  name: Test\n  code: test\noutput: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ArtifactLoadError, match="ausente"):
        builder.build_from_paths("profile.yaml", "missing")
    (tmp_path / "structural").mkdir()
    (tmp_path / "structural" / "screen_index.json").write_text(
        "{bad",
        encoding="utf-8",
    )
    with pytest.raises(ArtifactLoadError, match="corrupto"):
        builder.build_from_paths("profile.yaml", "structural")
    (tmp_path / "profile.yaml").write_text("erp: [", encoding="utf-8")
    with pytest.raises(ArtifactLoadError, match="Perfil inválido"):
        builder.build_from_paths("profile.yaml")


def test_nested_expand_menus_become_recursive_modules_and_use_most_specific_parent():
    profile = {
        "erp": {"name": "Fictional ERP", "code": "fictional"},
        "navigation": {"home_url": "/app/home"},
    }
    root_state = "/app/home#state:root-sales"
    nested_state = "/app/home#state:nested-tracking"
    artifacts = {
        "screen_index.json": {"screens": [
            {"route": "/app/home", "title": "Home"},
            {"route": "/app/sales/orders", "title": "Orders"},
            {"route": "/app/sales/tracking/external", "title": "External tracking"},
        ]},
        "routes_graph.json": {
            "nodes": [
                {"id": "/app/home", "route": "/app/home", "source_module": "root"},
                {
                    "id": root_state,
                    "route": root_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 1,
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Sales",
                                        "selector": "nav > menu:nth(1)",
                                    }
                                }
                            ],
                        },
                    },
                },
                {
                    "id": nested_state,
                    "route": nested_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 2,
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Sales",
                                        "selector": "nav > menu:nth(1)",
                                    }
                                },
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Tracking",
                                        "selector": "nav > menu:nth(1) > submenu",
                                    }
                                },
                            ],
                        },
                    },
                },
                {"id": "/app/sales/orders", "route": "/app/sales/orders"},
                {"id": "/app/sales/tracking/external", "route": "/app/sales/tracking/external"},
            ],
            "edges": [
                {
                    "source": "/app/home",
                    "target": root_state,
                    "label": "Sales",
                    "kind": "ui_event",
                    "metadata": {"event_category": "expand_menu", "selector": "nav > menu:nth(1)"},
                },
                {
                    "source": root_state,
                    "target": "/app/sales/orders",
                    "label": "Orders",
                    "kind": "ui_event_discovered_href",
                    "metadata": {},
                },
                {
                    "source": "/app/home",
                    "target": nested_state,
                    "label": "Tracking",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "nav > menu:nth(1) > submenu",
                    },
                },
                {
                    "source": nested_state,
                    "target": "/app/sales/tracking/external",
                    "label": "External",
                    "kind": "ui_event_discovered_href",
                    "metadata": {},
                },
            ],
        },
        "state_registry.json": {"states": []},
        "state_flow_graph.json": {"states": [], "transitions": []},
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }

    kb = CanonicalKnowledgeBuilder().build(profile, artifacts)
    modules = {module.name: module for module in kb.modules}

    assert set(modules) == {"Sales", "Tracking"}

    sales = modules["Sales"]
    tracking = modules["Tracking"]

    assert sales.parent_module_id is None
    assert sales.depth == 0
    assert sales.navigation_path == ["Sales"]

    assert tracking.parent_module_id == sales.id
    assert tracking.depth == 1
    assert tracking.navigation_path == ["Sales", "Tracking"]

    assert next(
        screen
        for screen in kb.screens
        if screen.route == "/app/sales/orders"
    ).module_id == sales.id

    assert next(
        screen
        for screen in kb.screens
        if screen.route == "/app/sales/tracking/external"
    ).module_id == tracking.id


def test_case_distinct_top_level_modules_do_not_collide():
    profile = {
        "erp": {"name": "Fictional ERP", "code": "fictional"},
        "navigation": {"home_url": "/app/home"},
    }

    def state_node(state_id, label, selector):
        return {
            "id": state_id,
            "route": state_id,
            "metadata": {
                "kind": "ui_state",
                "base_route": "/app/home",
                "path": {
                    "depth": 1,
                    "steps": [
                        {
                            "event": {
                                "event_type": "expand_menu",
                                "label": label,
                                "selector": selector,
                            }
                        }
                    ],
                },
            },
        }

    lower = "/app/home#state:lower"
    upper = "/app/home#state:upper"
    artifacts = {
        "screen_index.json": {"screens": [
            {"route": "/app/home", "title": "Home"},
            {"route": "/app/rentas/cajas", "title": "Boxes"},
            {"route": "/app/rentas/conceptos", "title": "Concepts"},
        ]},
        "routes_graph.json": {
            "nodes": [
                {"id": "/app/home", "route": "/app/home", "source_module": "root"},
                state_node(lower, "rentas", "nav > menu:nth(6)"),
                state_node(upper, "Rentas", "nav > menu:nth(7)"),
                {"id": "/app/rentas/cajas", "route": "/app/rentas/cajas"},
                {"id": "/app/rentas/conceptos", "route": "/app/rentas/conceptos"},
            ],
            "edges": [
                {
                    "source": "/app/home",
                    "target": lower,
                    "label": "rentas",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "nav > menu:nth(6)",
                    },
                },
                {
                    "source": lower,
                    "target": "/app/rentas/cajas",
                    "label": "Boxes",
                    "metadata": {},
                },
                {
                    "source": "/app/home",
                    "target": upper,
                    "label": "Rentas",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "nav > menu:nth(7)",
                    },
                },
                {
                    "source": upper,
                    "target": "/app/rentas/conceptos",
                    "label": "Concepts",
                    "metadata": {},
                },
            ],
        },
        "state_registry.json": {"states": []},
        "state_flow_graph.json": {"states": [], "transitions": []},
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }

    kb = CanonicalKnowledgeBuilder().build(profile, artifacts)
    modules = {module.name: module for module in kb.modules}
    assert set(modules) == {"rentas", "Rentas"}
    assert modules["rentas"].id != modules["Rentas"].id
    assert next(
        screen for screen in kb.screens if screen.route == "/app/rentas/cajas"
    ).module_id == modules["rentas"].id
    assert next(
        screen for screen in kb.screens if screen.route == "/app/rentas/conceptos"
    ).module_id == modules["Rentas"].id


def test_top_level_module_without_functional_screen_is_not_published():
    profile = {
        "erp": {"name": "Fictional ERP", "code": "fictional"},
        "navigation": {"home_url": "/app/home"},
    }
    unavailable_state = "/app/home#state:unavailable"
    artifacts = {
        "screen_index.json": {"screens": [{"route": "/app/home", "title": "Home"}]},
        "routes_graph.json": {
            "nodes": [
                {"id": "/app/home", "route": "/app/home", "source_module": "root"},
                {
                    "id": unavailable_state,
                    "route": unavailable_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 1,
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Unavailable",
                                        "selector": "nav > menu:nth(1)",
                                    }
                                }
                            ],
                        },
                    },
                },
                {"id": "/app/unavailable/a", "route": "/app/unavailable/a", "status": "not_found"},
            ],
            "edges": [
                {
                    "source": "/app/home",
                    "target": unavailable_state,
                    "label": "Unavailable",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "nav > menu:nth(1)",
                    },
                },
                {
                    "source": unavailable_state,
                    "target": "/app/unavailable/a",
                    "label": "A",
                    "metadata": {},
                },
            ],
        },
        "state_registry.json": {"states": []},
        "state_flow_graph.json": {"states": [], "transitions": []},
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }

    builder = CanonicalKnowledgeBuilder()
    kb = builder.build(profile, artifacts)
    assert kb.modules == []
    assert any(warning.code == "module_without_functional_screen" for warning in kb.build_warnings)
    assert builder.omitted["modules_without_functional_screen"] == 1


def test_evidence_path_uses_custom_structural_artifact_directory(tmp_path):
    from tests.canonical_fixtures import fictional_artifacts, fictional_profile

    artifact_dir = tmp_path / "data" / "runs" / "pipeline" / "job-123" / "processed" / "structural"
    knowledge = CanonicalKnowledgeBuilder(tmp_path).build(
        fictional_profile(),
        fictional_artifacts(),
        artifact_dir=artifact_dir,
    )

    assert knowledge.evidence
    assert all(
        item.artifact_path.startswith(
            "data/runs/pipeline/job-123/processed/structural/"
        )
        for item in knowledge.evidence
    )


def test_top_level_modules_have_root_hierarchy_metadata():
    kb = build()

    assert kb.modules

    for module in kb.modules:
        assert module.parent_module_id is None
        assert module.depth == 0
        assert module.navigation_path == [module.name]


def test_recursive_module_hierarchy_supports_three_levels_and_descendant_only_screen():
    profile = {
        "erp": {
            "name": "Fictional ERP",
            "code": "fictional",
        },
        "navigation": {
            "home_url": "/app/home",
        },
    }

    sales_state = "/app/home#state:sales"
    tracking_state = "/app/home#state:sales-tracking"
    integrations_state = "/app/home#state:sales-tracking-integrations"

    sales_step = {
        "event": {
            "event_type": "expand_menu",
            "label": "Sales",
            "selector": "nav > menu:nth(1)",
        }
    }

    tracking_step = {
        "event": {
            "event_type": "expand_menu",
            "label": "Tracking",
            "selector": "nav > menu:nth(1) > submenu:nth(1)",
        }
    }

    integrations_step = {
        "event": {
            "event_type": "expand_menu",
            "label": "Integrations",
            "selector": (
                "nav > menu:nth(1) "
                "> submenu:nth(1) "
                "> submenu:nth(1)"
            ),
        }
    }

    artifacts = {
        "screen_index.json": {
            "screens": [
                {
                    "route": "/app/home",
                    "title": "Home",
                },
                {
                    "route": (
                        "/app/sales/tracking/"
                        "integrations/external"
                    ),
                    "title": "External systems",
                },
            ]
        },
        "routes_graph.json": {
            "nodes": [
                {
                    "id": "/app/home",
                    "route": "/app/home",
                    "source_module": "root",
                },
                {
                    "id": sales_state,
                    "route": sales_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 1,
                            "steps": [
                                sales_step,
                            ],
                        },
                    },
                },
                {
                    "id": tracking_state,
                    "route": tracking_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 2,
                            "steps": [
                                sales_step,
                                tracking_step,
                            ],
                        },
                    },
                },
                {
                    "id": integrations_state,
                    "route": integrations_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 3,
                            "steps": [
                                sales_step,
                                tracking_step,
                                integrations_step,
                            ],
                        },
                    },
                },
                {
                    "id": (
                        "/app/sales/tracking/"
                        "integrations/external"
                    ),
                    "route": (
                        "/app/sales/tracking/"
                        "integrations/external"
                    ),
                },
            ],
            "edges": [
                {
                    "source": "/app/home",
                    "target": sales_state,
                    "label": "Sales",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                    },
                },
                {
                    "source": sales_state,
                    "target": tracking_state,
                    "label": "Tracking",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                    },
                },
                {
                    "source": tracking_state,
                    "target": integrations_state,
                    "label": "Integrations",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                    },
                },
                {
                    "source": integrations_state,
                    "target": (
                        "/app/sales/tracking/"
                        "integrations/external"
                    ),
                    "label": "External systems",
                    "kind": "ui_event_discovered_href",
                    "metadata": {},
                },
            ],
        },
        "state_registry.json": {
            "states": [],
        },
        "state_flow_graph.json": {
            "states": [],
            "transitions": [],
        },
        "event_policy_audit.json": {
            "screens": [],
        },
        "ui_event_execution_audit.json": {},
    }

    first = CanonicalKnowledgeBuilder().build(
        profile,
        artifacts,
    )
    second = CanonicalKnowledgeBuilder().build(
        profile,
        artifacts,
    )

    modules = {
        module.name: module
        for module in first.modules
    }

    assert set(modules) == {
        "Sales",
        "Tracking",
        "Integrations",
    }

    sales = modules["Sales"]
    tracking = modules["Tracking"]
    integrations = modules["Integrations"]

    assert sales.parent_module_id is None
    assert sales.depth == 0
    assert sales.navigation_path == [
        "Sales",
    ]

    assert tracking.parent_module_id == sales.id
    assert tracking.depth == 1
    assert tracking.navigation_path == [
        "Sales",
        "Tracking",
    ]

    assert integrations.parent_module_id == tracking.id
    assert integrations.depth == 2
    assert integrations.navigation_path == [
        "Sales",
        "Tracking",
        "Integrations",
    ]

    external = next(
        screen
        for screen in first.screens
        if screen.route
        == "/app/sales/tracking/integrations/external"
    )

    assert external.module_id == integrations.id

    # Parents without direct functional screens remain published
    # because their descendant subtree contains one.
    assert sales.id in {
        module.id
        for module in first.modules
    }
    assert tracking.id in {
        module.id
        for module in first.modules
    }

    # Canonical hierarchy and IDs remain deterministic.
    second_modules = {
        module.name: module
        for module in second.modules
    }

    assert second_modules["Sales"].id == sales.id
    assert second_modules["Tracking"].id == tracking.id
    assert second_modules["Integrations"].id == integrations.id
    assert first.knowledge_version == second.knowledge_version


def test_realistic_long_menu_selectors_survive_navigation_privacy_filter():
    profile = {
        "erp": {"name": "Fictional ERP", "code": "fictional"},
        "navigation": {"home_url": "/app/home"},
    }
    root_selector = (
        "app-root > layout > admin-layout > fuse-vertical-navigation > div > "
        "div:nth-of-type(2) > fuse-vertical-navigation-group-item > "
        "fuse-vertical-navigation-collapsable-item:nth-of-type(10)"
    )
    child_selector = (
        "admin-layout > fuse-vertical-navigation > div > div:nth-of-type(2) > "
        "fuse-vertical-navigation-group-item > "
        "fuse-vertical-navigation-collapsable-item:nth-of-type(10) > "
        "div:nth-of-type(2) > "
        "fuse-vertical-navigation-collapsable-item:nth-of-type(4)"
    )
    root_state = "/app/home#state:tramites"
    child_state = "/app/home#state:rastrear"
    artifacts = {
        "screen_index.json": {
            "screens": [
                {"route": "/app/home", "title": "Home"},
                {"route": "/app/tramites/main", "title": "Tramites"},
                {"route": "/app/tramites/rastrear", "title": "Rastrear"},
            ]
        },
        "routes_graph.json": {
            "nodes": [
                {"id": "/app/home", "route": "/app/home"},
                {
                    "id": root_state,
                    "route": root_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Tramites",
                                        "selector": root_selector,
                                    }
                                }
                            ]
                        },
                    },
                },
                {
                    "id": child_state,
                    "route": child_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Tramites",
                                        "selector": root_selector,
                                    }
                                },
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Rastrear",
                                        "selector": child_selector,
                                    }
                                },
                            ]
                        },
                    },
                },
                {"id": "/app/tramites/main", "route": "/app/tramites/main"},
                {"id": "/app/tramites/rastrear", "route": "/app/tramites/rastrear"},
            ],
            "edges": [
                {"source": root_state, "target": "/app/tramites/main", "metadata": {}},
                {"source": child_state, "target": "/app/tramites/rastrear", "metadata": {}},
            ],
        },
        "state_registry.json": {"states": []},
        "state_flow_graph.json": {"states": [], "transitions": []},
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }

    kb = CanonicalKnowledgeBuilder().build(profile, artifacts)
    modules = {module.name: module for module in kb.modules}

    assert modules["Tramites"].metadata["navigation_origin"] == root_selector
    assert modules["Tramites"].metadata["navigation_origin_path"] == root_selector
    assert modules["Rastrear"].metadata["navigation_origin"] == child_selector
    assert modules["Rastrear"].metadata["navigation_origin_path"] == (
        f"{root_selector} || {child_selector}"
    )


def test_missing_menu_selector_is_not_persisted_as_replay_locator():
    profile = {
        "erp": {"name": "Fictional ERP", "code": "fictional"},
        "navigation": {"home_url": "/app/home"},
    }
    state = "/app/home#state:missing-selector"
    artifacts = {
        "screen_index.json": {
            "screens": [
                {"route": "/app/home", "title": "Home"},
                {"route": "/app/module/a", "title": "A"},
            ]
        },
        "routes_graph.json": {
            "nodes": [
                {"id": "/app/home", "route": "/app/home"},
                {
                    "id": state,
                    "route": state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Module A",
                                        "selector": "",
                                    }
                                }
                            ]
                        },
                    },
                },
                {"id": "/app/module/a", "route": "/app/module/a"},
            ],
            "edges": [
                {"source": state, "target": "/app/module/a", "metadata": {}}
            ],
        },
        "state_registry.json": {"states": []},
        "state_flow_graph.json": {"states": [], "transitions": []},
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }

    kb = CanonicalKnowledgeBuilder().build(profile, artifacts)
    module = next(item for item in kb.modules if item.name == "Module A")

    assert module.metadata == {}
