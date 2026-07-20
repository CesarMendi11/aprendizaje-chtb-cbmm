# Fase 3C.1: proyección segura a Neo4j

PostgreSQL continúa siendo la fuente operativa de verdad. Neo4j es una
proyección derivada, reconstruible y versionada; no sustituye revisiones,
estados ni historial. Solo se proyectan `approved` y `corrected`. Para estos
últimos se usa siempre `effective_payload`. `pending_review` y `rejected` se
excluyen.

## Modelo y seguridad

Cada nodo usa `:ERPAssistantEntity` y una etiqueta canónica fija. Su identidad
combina ERP, `knowledge_version` y `canonical_id`; también conserva tipo, hash,
estado de revisión, instante de proyección y `managed_by=erp_assistant`. Las
propiedades se seleccionan mediante listas blancas. No se almacenan payloads
completos, HTML, screenshots, credenciales, filas ni datos transaccionales.

Las relaciones representan únicamente referencias canónicas existentes entre
dos nodos elegibles. Un extremo no proyectado causa una omisión agregada, nunca
un placeholder. `MERGE` parametrizado hace idempotentes nodos y relaciones.

La reconciliación opcional se limita a nodos con los tres valores exactos:
`managed_by=erp_assistant`, ERP solicitado y versión solicitada. Nunca ejecuta
un borrado global ni elimina otras versiones, otros ERP o nodos externos.

## Configuración

```bash
export ERP_ASSISTANT_NEO4J_URI=bolt://127.0.0.1:7687
export ERP_ASSISTANT_NEO4J_USER=neo4j
export ERP_ASSISTANT_NEO4J_PASSWORD='contraseña-local'
export ERP_ASSISTANT_NEO4J_DATABASE=neo4j
```

FastAPI no lee estas variables ni abre conexiones Neo4j. Los clientes se crean
solo al ejecutar los scripts. La contraseña nunca se imprime.

## Operación

```bash
python -m scripts.neo4j_status
python -m scripts.bootstrap_neo4j
python -m scripts.sync_approved_to_neo4j --dry-run --pretty
python -m scripts.sync_approved_to_neo4j --pretty
python -m scripts.inspect_neo4j_projection
```

El bootstrap crea constraints e índices administrados con `IF NOT EXISTS`, sin
APOC. El dry-run consulta PostgreSQL y mapea el grafo sin conectarse a Neo4j ni
modificar `sync_jobs`. Una ejecución vacía se rechaza salvo `--allow-empty`.
`--replace-version` pide confirmación, excepto con `--yes`.

El `SyncJob` Neo4j existente cambia a `running` y luego `succeeded` o `failed`,
incrementa intentos y guarda solo conteos, lote y hash de proyección. Una
repetición después de éxito es un nuevo intento idempotente sobre el mismo job.
El trabajo ChromaDB no se modifica.

## Pruebas y siguiente fase

La suite normal usa clientes falsos y no necesita servidor. La integración real
es opcional mediante `TEST_NEO4J_URI`, `TEST_NEO4J_USER`,
`TEST_NEO4J_PASSWORD` y `TEST_NEO4J_DATABASE`, siempre con un namespace
sintético aislado. La Fase 3C.2 podrá añadir orquestación explícita y políticas
de operación; ChromaDB, embeddings, LLM y Graph RAG permanecen fuera de 3C.1.
