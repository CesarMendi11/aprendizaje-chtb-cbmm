import os
import json
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI")
NEO4J_USER = os.getenv("NEO4J_USER")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

MENU_FILE = sorted(Path("data/raw/playwright").glob("menu_map_*.json"))[-1]

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USER, NEO4J_PASSWORD)
)

def clear_database(tx):
    tx.run("MATCH (n) DETACH DELETE n")

def load_menu(tx, data):
    erp_name = "ERP Cuerpo de Bomberos de Machala"

    tx.run("""
        MERGE (erp:ERP {name: $erp_name})
        SET erp.base_url = $base_url,
            erp.source = $source,
            erp.updated_at = $timestamp
    """, erp_name=erp_name, base_url=data["base_url"], source=data["source"], timestamp=data["timestamp"])

    for module in data["modules"]:
        tx.run("""
            MATCH (erp:ERP {name: $erp_name})
            MERGE (m:Module {name: $module_name})
            MERGE (erp)-[:HAS_MODULE]->(m)
        """, erp_name=erp_name, module_name=module["name"])

        for sub in module["submodules"]:
            tx.run("""
                MATCH (m:Module {name: $module_name})
                MERGE (s:Submodule {name: $submodule_name, url: $url})
                SET s.classes = $classes
                MERGE (m)-[:HAS_SUBMODULE]->(s)
            """, module_name=module["name"], submodule_name=sub["name"], url=sub["url"], classes=sub["classes"])

try:
    with open(MENU_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    with driver.session() as session:
        session.execute_write(clear_database)
        session.execute_write(load_menu, data)

    print("✅ Menú cargado en Neo4j")
    print(f"Archivo usado: {MENU_FILE}")

finally:
    driver.close()