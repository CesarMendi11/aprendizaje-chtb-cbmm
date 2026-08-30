# Test suite layout

La suite se organiza por responsabilidad del sistema para que la ubicación de cada prueba refleje el componente que certifica:

- `api/`: contratos HTTP, routers y servicios administrativos expuestos por FastAPI.
- `config/`: clases de settings y carga de perfiles/configuración.
- `crawler/`: crawling, eventos, rutas, estados, extracción y evidencia de UI/red.
- `canonical/`: construcción, validación, materialización e importación del conocimiento canónico.
- `database/`: modelos y migraciones PostgreSQL de propósito general.
- `governance/`: revisión, promoción, reconciliación y versionado gobernado.
- `hybrid/`: planificación, recuperación, conversación y decisión de respuesta M3.
- `pipeline/`: jobs, runner, executors, recovery y sincronizaciones orquestadas.
- `projections/`: Neo4j, Chroma y servicios de proyección/reemplazo.
- `semantic/`: inferencia semántica, lifecycle semántico, Ollama y servicios semánticos.
- `scripts/`: contratos de las herramientas CLI y de la organización de `scripts/`.
- `architecture/`: fronteras/imports y pruebas de generalización transversal.
- `fixtures/`: builders y fixtures compartidos entre dominios de prueba.

Los archivos `test_*.py` no deben volver a la raíz de `tests/`; las pruebas nuevas deben ubicarse en el dominio que certifican.
