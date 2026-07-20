# Fase 3B.1: PostgreSQL y revisión humana

PostgreSQL es la fuente de verdad **operativa** del conocimiento importado y
revisado. No es la base transaccional del ERP institucional: no contiene sus
usuarios, credenciales ni registros de negocio. Tampoco sustituye a Neo4j o
ChromaDB; esos motores se incorporarán después como proyecciones derivadas de
elementos aprobados o corregidos.

## Modelo

- `erp_systems`: registro seguro del ERP, sin credenciales.
- `import_runs`: auditoría de intentos de importación.
- `knowledge_versions`: versiones canónicas inmutables; una sola activa por ERP.
- `knowledge_items`: representación genérica de todos los tipos canónicos.
- `review_actions`: historial append-only de decisiones y correcciones.
- `sync_jobs`: trabajos pendientes para Neo4j y ChromaDB, sin ejecución.

`source_payload` conserva el objeto automático original y nunca se sobrescribe.
Una corrección vive en `review_actions.corrected_payload`. El payload efectivo
es la última corrección vigente o, si no existe, el original.

El `content_hash` SHA-256 excluye timestamps y metadatos de revisión. Al importar
otra versión, una decisión se arrastra solamente si coinciden tipo, ID canónico
y hash. Si cambia el contenido, el elemento vuelve a `pending_review`.

## Desarrollo local

La contraseña por defecto siguiente es exclusivamente local. Debe cambiarse en
cualquier otro entorno.

```bash
docker compose -f docker-compose.postgres.yml up -d

export ERP_ASSISTANT_DATABASE_URL="postgresql+psycopg://erp_assistant:erp_assistant_local@127.0.0.1:5434/erp_assistant"

alembic upgrade head
```

La URL se toma del entorno; `alembic.ini` no contiene credenciales. El API
FastAPI no necesita esta variable y continúa leyendo artefactos estructurales.

## Importación

La carga valida el documento canónico completo antes de abrir una ejecución de
importación o escribir elementos. Un documento con valores sensibles se
rechaza; PostgreSQL no aplica filtros silenciosos para ocultar una fuente
inválida. Neo4j y ChromaDB también deben recibir exclusivamente conocimiento
canónico sanitizado.

```bash
python -m scripts.import_canonical_to_postgres \
  --knowledge data/processed/canonical/knowledge.json \
  --manifest data/processed/canonical/manifest.json \
  --build-report data/processed/canonical/build_report.json
```

La repetición de una versión es `skipped`. `--dry-run` valida y calcula sin
escribir; `--no-activate` importa sin activar; `--no-sync-jobs` evita preparar
trabajos futuros; `--strict` considera las advertencias un error operativo.

```bash
python -m scripts.database_status
python -m scripts.inspect_postgres_knowledge
```

## Revisión

```bash
python -m scripts.review_knowledge_item list --status pending_review
python -m scripts.review_knowledge_item show --item-id UUID
python -m scripts.review_knowledge_item approve --item-id UUID --reviewer operador
python -m scripts.review_knowledge_item reject --item-id UUID --reviewer operador --notes "Motivo"
python -m scripts.review_knowledge_item correct --item-id UUID \
  --correction-file ./correction.json --reviewer operador --notes "Motivo"
python -m scripts.review_knowledge_item reset --item-id UUID
python -m scripts.review_knowledge_item history --item-id UUID
```

Las operaciones mutativas muestran un resumen y requieren confirmación. `--yes`
permite automatización. La corrección debe ser un JSON local, pequeño, válido
para el tipo canónico, con el mismo ID y relaciones críticas. Se rechazan
secretos, correos, IP, tokens, HTML ejecutable y claves operativas.

La auditoría no admite actualización ni eliminación: SQLAlchemy lo impide en la
aplicación y PostgreSQL instala un trigger append-only. La concurrencia se
protege mediante bloqueo de fila y `review_revision`.

## Conocimiento efectivo y privacidad

`EffectiveKnowledgeService` devuelve el original, la corrección vigente, el
payload efectivo y el historial. Su proyección para sincronización incluye
exclusivamente `approved` y `corrected`; nunca `pending_review` o `rejected`.
La exportación efectiva se genera en memoria y no modifica los JSON canónicos.

No se almacenan preguntas, mensajes, contraseñas, tokens, cookies, correos, IP,
HTML completo, screenshots, filas reales ni valores transaccionales concretos.
`main_content_text` es un resumen de etiquetas estructurales, no texto visible
copiado de la pantalla. Nunca se debe aprobar un elemento que contenga filas
reales.

Los artefactos estructurales crudos pueden conservar evidencia sensible y por
eso deben permanecer locales, ignorados por Git y con acceso restringido. Los
errores persistibles deben ser breves y sanitizados.

## Migraciones y diagnóstico

```bash
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

Si falta configuración, verifique `ERP_ASSISTANT_DATABASE_URL`. Si falla la
conexión, confirme el healthcheck con
`docker compose -f docker-compose.postgres.yml ps`. Si aparece una versión
duplicada, es normal que la importación termine como `skipped`.

Las pruebas PostgreSQL reales usan `TEST_DATABASE_URL`; sin ella se omiten y la
suite normal utiliza SQLite temporal.

## Siguiente fase

Quedan fuera: workers reales, Neo4j, ChromaDB, embeddings, Ollama/LLM, Graph RAG,
RBAC, endpoints administrativos, interfaz de revisión y almacenamiento de
conversaciones. `/api/chat` todavía no consume PostgreSQL.
