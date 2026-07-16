from __future__ import annotations


def fictional_profile():
    return {"erp": {"name": "Northwind Operations", "code": "northwind", "base_url": "https://erp.example.test"}}


def fictional_artifacts():
    screens = [
        {"route": "/app/home", "title": "Dashboard", "main_visible_text": "Welcome 10.1.2.3 owner@example.test", "regions": {}},
        {"route": "/app/inventory/products", "title": "Products", "inputs": [{"label": "SKU", "name": "sku"}, {"label": "Secret", "region": "volatile"}], "buttons": [{"text": "Search"}], "tables": [{"name": "Products", "headers": ["SKU", "Name"]}], "local_links": [{"text": "Suppliers", "href": "/app/purchasing/suppliers"}]},
        {"route": "/app/purchasing/suppliers", "title": "Suppliers"},
    ]
    root = "raw:root"; product = "raw:product"
    return {
        "screen_index.json": {"screens": screens},
        "routes_graph.json": {"nodes": [
            {"route": "/app/home", "source_module": "root"},
            {"route": "/app/inventory/products", "source_module": "Inventory"},
            {"route": "/app/purchasing/suppliers", "source_module": "Purchasing"},
        ], "edges": []},
        "state_registry.json": {"states": [
            {"state_id": root, "route": "/app/home", "title": "Dashboard", "structural_signature": "root", "metadata": {"depth": 0}},
            {"state_id": product, "route": "/app/inventory/products", "title": "Products", "structural_signature": "product", "metadata": {"depth": 0}},
        ]},
        "state_flow_graph.json": {"states": [], "transitions": [{"source_state_id": root, "target_state_id": product, "event": {"event_type": "navigation_link", "label": "Products", "decision": "allow", "metadata": {"region": "global_navigation"}}, "changed_route": True, "observed": True}]},
        "event_policy_audit.json": {"screens": []},
        "ui_event_execution_audit.json": {},
    }
