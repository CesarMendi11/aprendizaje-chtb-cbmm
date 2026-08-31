# Test suite layout

La suite se organiza por responsabilidad arquitectónica para que la ubicación de cada prueba refleje la parte productiva que certifica:

- `acquisition/`: acceso al ERP, navegador, discovery, extracción, crawling, políticas, artefactos y calidad de crawl.
- `structural/canonical/`: construcción, validación, materialización, importación, merge y reconciliación del conocimiento canónico.
- `structural/governance/`: revisión, diferencias, removals, publicación y promoción de conocimiento estructural.
- `semantic/`: elegibilidad, evidencia, inferencia, lifecycle, revisión semántica y generación estructurada con Ollama.
- `retrieval/`: planificación, resolución de entidades, ranking, evidencia, conversación y respuesta Hybrid M3.
- `orchestration/`: `PipelineJob`, runner, dispatcher/executors y recovery.
- `persistence/`: modelos y migraciones PostgreSQL.
- `projections/`: Neo4j, Chroma y reemplazo de proyecciones derivadas.
- `api/`: contratos HTTP, routers y servicios administrativos expuestos por FastAPI.
- `config/`: settings y carga de perfiles.
- `scripts/`: contratos de las herramientas CLI y organización de `scripts/`.
- `architecture/`: fronteras de imports, generalización y layout.
- `fixtures/`: builders y datos compartidos por las pruebas.
- `certification/`: matrices/datos de certificación end-to-end que consumen los runners de `scripts/certification/`.

Los archivos `test_*.py` no deben volver a la raíz de `tests/`; las pruebas nuevas deben ubicarse junto a la responsabilidad arquitectónica que certifican.
