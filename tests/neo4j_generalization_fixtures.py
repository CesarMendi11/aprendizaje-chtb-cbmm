from __future__ import annotations

from datetime import datetime, timezone

from src.knowledge.canonical.enums import ControlType, EvidenceType
from src.knowledge.canonical.models import (
    CanonicalKnowledgeBase,
    Control,
    ERPSystem,
    Event,
    Evidence,
    Field,
    Link,
    Module,
    Screen,
    Table,
    TableColumn,
    Transition,
    UIState,
)

NOVA_ROUTE = "/inventory/products"


def nova_retail_knowledge() -> CanonicalKnowledgeBase:
    erp_id = "erp:nova-retail"
    module_id = "module:nova-inventory"
    screen_id = "screen:nova-products"
    other_screen_id = "screen:nova-orders"
    root_state_id = "ui_state:nova-products-root"
    detail_state_id = "ui_state:nova-products-detail"
    other_state_id = "ui_state:nova-orders-root"
    event_id = "event:nova-open-product"
    table_id = "table:nova-products"

    controls = [
        Control(
            id="control:nova-search",
            screen_id=screen_id,
            label="Search",
            normalized_label="search",
            control_type=ControlType.BUTTON,
            region="main_content",
        ),
        Control(
            id="control:nova-add",
            screen_id=screen_id,
            label="Add product",
            normalized_label="add product",
            control_type=ControlType.BUTTON,
            region="main_content",
        ),
        Control(
            id="control:nova-filter",
            screen_id=screen_id,
            label="Filter catalog",
            normalized_label="filter catalog",
            control_type=ControlType.DROPDOWN,
            region="main_content",
        ),
        Control(
            id="control:nova-placeholder",
            screen_id=screen_id,
            label="unlabeled control",
            normalized_label="unlabeled control",
            control_type=ControlType.BUTTON,
            region="main_content",
        ),
    ]
    columns = [
        TableColumn(
            id="table_column:nova-sku",
            table_id=table_id,
            name="SKU",
            normalized_name="sku",
            position=0,
        ),
        TableColumn(
            id="table_column:nova-name",
            table_id=table_id,
            name="Product name",
            normalized_name="product name",
            position=1,
        ),
        TableColumn(
            id="table_column:nova-stock",
            table_id=table_id,
            name="Stock",
            normalized_name="stock",
            position=2,
        ),
    ]
    return CanonicalKnowledgeBase(
        schema_version="1.0.0",
        knowledge_version="nova-version-one",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        generator_version="synthetic-test",
        source_profile="nova-retail-test",
        source_artifacts=[],
        source_artifact_hashes={},
        erp_system=ERPSystem(
            id=erp_id,
            slug="nova-retail",
            name="NovaRetail ERP",
            profile_name="nova-retail-test",
        ),
        modules=[
            Module(
                id=module_id,
                erp_id=erp_id,
                name="Inventory",
                normalized_name="inventory",
                route_prefix="/inventory",
            )
        ],
        screens=[
            Screen(
                id=screen_id,
                erp_id=erp_id,
                module_id=module_id,
                title="Products",
                normalized_title="products",
                route=NOVA_ROUTE,
                main_content_text="Products | Search | Add product | SKU | Product name | Stock",
            ),
            Screen(
                id=other_screen_id,
                erp_id=erp_id,
                module_id=module_id,
                title="Orders",
                normalized_title="orders",
                route="/sales/orders",
                main_content_text="Orders",
            ),
        ],
        ui_states=[
            UIState(
                id=root_state_id,
                screen_id=screen_id,
                route=NOVA_ROUTE,
                depth=0,
                title="Products",
                structural_fingerprint="nova-products-root",
                is_route_root=True,
            ),
            UIState(
                id=detail_state_id,
                screen_id=screen_id,
                route=NOVA_ROUTE,
                depth=1,
                title="Product details",
                structural_fingerprint="nova-products-detail",
            ),
            UIState(
                id=other_state_id,
                screen_id=other_screen_id,
                route="/sales/orders",
                depth=0,
                title="Orders",
                structural_fingerprint="nova-orders-root",
                is_route_root=True,
            ),
        ],
        fields=[
            Field(
                id="field:nova-sku",
                screen_id=screen_id,
                label="SKU",
                normalized_label="sku",
                name="sku",
                input_type="text",
            ),
            Field(
                id="field:nova-category",
                screen_id=screen_id,
                label="Category",
                normalized_label="category",
                name="category",
                input_type="select",
            ),
        ],
        controls=controls,
        tables=[
            Table(
                id=table_id,
                screen_id=screen_id,
                name="Product catalog",
                normalized_name="product catalog",
                column_ids=[column.id for column in columns],
            )
        ],
        table_columns=columns,
        links=[
            Link(
                id="link:nova-details",
                screen_id=screen_id,
                label="Product details",
                normalized_label="product details",
                target_route="/inventory/products/details",
                region="main_content",
            ),
            Link(
                id="link:nova-global",
                screen_id=screen_id,
                label="Company home",
                normalized_label="company home",
                target_route="/home",
                region="global_navigation",
            ),
        ],
        events=[
            Event(
                id=event_id,
                screen_id=screen_id,
                source_state_id=root_state_id,
                label="Open product",
                normalized_label="open product",
                category="state_change",
                policy_decision="allow",
                region="main_content",
            )
        ],
        transitions=[
            Transition(
                id="transition:nova-internal",
                source_state_id=root_state_id,
                target_state_id=detail_state_id,
                event_id=event_id,
                category="state_change",
                changed=True,
                route_changed=False,
                depth=1,
            ),
            Transition(
                id="transition:nova-cross-screen",
                source_state_id=root_state_id,
                target_state_id=other_state_id,
                event_id=event_id,
                category="navigation",
                changed=True,
                route_changed=True,
            ),
        ],
        evidence=[
            Evidence(
                id="evidence:nova-products",
                evidence_type=EvidenceType.STRUCTURAL_JSON,
                artifact_path="synthetic/nova-products.json",
                source_entity_type="screen",
                source_entity_id=screen_id,
            )
        ],
        statistics={
            "modules": 1,
            "screens": 2,
            "ui_states": 3,
            "fields": 2,
            "controls": 4,
            "tables": 1,
            "table_columns": 3,
            "links": 2,
            "events": 1,
            "transitions": 2,
            "evidence": 1,
        },
    )
