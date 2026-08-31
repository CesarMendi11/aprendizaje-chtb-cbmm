MANAGED_SCHEMA = (
    "CREATE CONSTRAINT erp_assistant_node_key IF NOT EXISTS "
    "FOR (n:ERPAssistantEntity) REQUIRE n.node_key IS UNIQUE",
    "CREATE INDEX erp_assistant_canonical_id IF NOT EXISTS "
    "FOR (n:ERPAssistantEntity) ON (n.canonical_id)",
    "CREATE INDEX erp_assistant_erp_id IF NOT EXISTS FOR (n:ERPAssistantEntity) ON (n.erp_id)",
    "CREATE INDEX erp_assistant_knowledge_version IF NOT EXISTS "
    "FOR (n:ERPAssistantEntity) ON (n.knowledge_version)",
    "CREATE INDEX erp_assistant_entity_type IF NOT EXISTS "
    "FOR (n:ERPAssistantEntity) ON (n.entity_type)",
    "CREATE INDEX erp_assistant_managed_by IF NOT EXISTS "
    "FOR (n:ERPAssistantEntity) ON (n.managed_by)",
    "CREATE INDEX erp_assistant_screen_route IF NOT EXISTS FOR (n:Screen) ON (n.route)",
)
