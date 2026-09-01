from __future__ import annotations

from pathlib import Path

from erp_assistant.structural.canonical.builder import CanonicalKnowledgeBuilder
from erp_assistant.structural.canonical.exporter import CanonicalKnowledgeExporter


def fictional_profile():
    return {
        "erp": {
            "name": "Northwind Operations",
            "code": "northwind",
            "base_url": "https://erp.example.test",
        },
        "navigation": {"home_url": "/app/home"},
    }


def fictional_artifacts():
    screens = [
        {
            "route": "/app/home",
            "title": "Dashboard",
            "main_visible_text": "Welcome 10.1.2.3 owner@example.test",
            "regions": {},
        },
        {
            "route": "/app/inventory/products",
            "title": "Products",
            "inputs": [{"label": "SKU", "name": "sku"}, {"label": "Secret", "region": "volatile"}],
            "buttons": [{"text": "Search"}],
            "tables": [{"name": "Products", "headers": ["SKU", "Name"]}],
            "local_links": [{"text": "Suppliers", "href": "/app/purchasing/suppliers"}],
        },
        {"route": "/app/purchasing/suppliers", "title": "Suppliers"},
    ]
    root = "raw:root"
    product = "raw:product"
    inventory_state = "/app/home#state:inventory"
    purchasing_state = "/app/home#state:purchasing"
    return {
        "screen_index.json": {"screens": screens},
        "routes_graph.json": {
            "nodes": [
                {"id": "/app/home", "route": "/app/home", "source_module": "root"},
                {
                    "id": inventory_state,
                    "route": inventory_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 1,
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Inventory",
                                        "selector": "nav > inventory",
                                    }
                                }
                            ],
                        },
                    },
                },
                {
                    "id": purchasing_state,
                    "route": purchasing_state,
                    "metadata": {
                        "kind": "ui_state",
                        "base_route": "/app/home",
                        "path": {
                            "depth": 1,
                            "steps": [
                                {
                                    "event": {
                                        "event_type": "expand_menu",
                                        "label": "Purchasing",
                                        "selector": "nav > purchasing",
                                    }
                                }
                            ],
                        },
                    },
                },
                {"id": "/app/inventory/products", "route": "/app/inventory/products"},
                {"id": "/app/purchasing/suppliers", "route": "/app/purchasing/suppliers"},
            ],
            "edges": [
                {
                    "source": "/app/home",
                    "target": inventory_state,
                    "label": "Inventory",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "nav > inventory",
                    },
                },
                {
                    "source": inventory_state,
                    "target": "/app/inventory/products",
                    "label": "Products",
                    "kind": "ui_event_discovered_href",
                    "metadata": {},
                },
                {
                    "source": "/app/home",
                    "target": purchasing_state,
                    "label": "Purchasing",
                    "kind": "ui_event",
                    "metadata": {
                        "event_category": "expand_menu",
                        "selector": "nav > purchasing",
                    },
                },
                {
                    "source": purchasing_state,
                    "target": "/app/purchasing/suppliers",
                    "label": "Suppliers",
                    "kind": "ui_event_discovered_href",
                    "metadata": {},
                },
            ],
        },
        "state_registry.json": {
            "states": [
                {
                    "state_id": root,
                    "route": "/app/home",
                    "title": "Dashboard",
                    "structural_signature": "root",
                    "metadata": {"depth": 0},
                },
                {
                    "state_id": product,
                    "route": "/app/inventory/products",
                    "title": "Products",
                    "structural_signature": "product",
                    "metadata": {"depth": 0},
                },
            ]
        },
        "state_flow_graph.json": {
            "states": [],
            "transitions": [
                {
                    "source_state_id": root,
                    "target_state_id": product,
                    "event": {
                        "event_type": "navigation_link",
                        "label": "Products",
                        "decision": "allow",
                        "metadata": {"region": "global_navigation"},
                    },
                    "changed_route": True,
                    "observed": True,
                }
            ],
        },
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }


def exported_fictional_canonical(target: Path) -> Path:
    builder = CanonicalKnowledgeBuilder()
    knowledge = builder.build(fictional_profile(), fictional_artifacts())
    CanonicalKnowledgeExporter().export(
        knowledge,
        target,
        build_report=builder.build_report(knowledge),
    )
    return target
