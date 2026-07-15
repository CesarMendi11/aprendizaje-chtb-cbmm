# Chat CBMM — Asistente funcional ERP con Graph RAG híbrido

Proyecto de desarrollo de un prototipo de asistente inteligente para soporte funcional en sistemas ERP institucionales.
El caso de estudio corresponde al sistema ERP del **Cuerpo de Bomberos Municipal de Machala (CBMM)**.

El objetivo del sistema es construir semiautomáticamente conocimiento funcional del ERP a partir de su interfaz, representarlo estructuralmente mediante grafos, enriquecerlo semánticamente con modelos locales y utilizarlo posteriormente en un asistente basado en recuperación híbrida Graph RAG.

---

## 1. Alcance del proyecto

El prototipo se enfoca exclusivamente en **soporte funcional del ERP**.

Incluye consultas relacionadas con:

- navegación dentro del ERP;
- ubicación de módulos;
- ubicación de pantallas;
- rutas de acceso;
- formularios;
- campos;
- botones;
- tablas;
- acciones disponibles;
- interpretación funcional básica de pantallas;
- orientación sobre cómo usar funcionalidades del sistema.

Queda fuera del alcance actual:

- generación o gestión formal de tickets;
- soporte técnico general;
- impresoras;
- internet;
- hardware;
- sistema operativo;
- problemas físicos de equipos;
- mesa de ayuda completa.

Las carpetas relacionadas con `tickets`, `soporte_ti` o `incidencias` se consideran por ahora material no activo o posible extensión futura.

---

## 2. Arquitectura general

La arquitectura prevista se organiza en las siguientes capas:

```text
ERP institucional
        ↓
Crawler estructural con Playwright
        ↓
Evidencias crudas
HTML + JSON Playwright + screenshots
        ↓
Procesamiento estructural
screen_index.json + routes_graph.json
        ↓
Normalización para grafo
        ↓
Neo4j
Grafo funcional del ERP
        ↓
LLM semántico + validación humana
        ↓
ChromaDB
Conocimiento semántico aprobado
        ↓
Graph RAG híbrido
Neo4j + ChromaDB + RBAC
        ↓
Asistente funcional ERP
```

---

## 3. Funciones de los modelos LLM

El proyecto contempla tres usos diferenciados de modelos de lenguaje locales mediante Ollama.

### 3.1. LLM Helper para el crawler

Apoya al crawler cuando aparecen incertidumbres durante la exploración.

Ejemplos:

- pantalla con demasiados botones ambiguos;
- formulario que requiere datos;
- acción potencialmente riesgosa;
- menú dinámico complejo;
- ruta que necesita una regla especial.

El LLM Helper no ejecuta acciones directamente. Solo propone reglas o sugerencias que deben ser revisadas.

```text
Crawler detecta incertidumbre
→ LLM Helper propone regla
→ humano valida
→ regla aprobada se incorpora al perfil YAML
```

### 3.2. LLM semántico

Convierte la estructura descubierta en conocimiento funcional comprensible.

Ejemplo:

```text
Ruta: /admin/cuentasxcobrar/comprobantes
Botones: Buscar, Open calendar
Campos: RUC, Núm. comprobante
Tabla: comprobantes emitidos
```

Puede proponer una descripción como:

```text
Pantalla utilizada para consultar comprobantes electrónicos emitidos,
filtrar por RUC, número de comprobante, fecha y estado.
```

Estas inferencias no se aprueban automáticamente. Deben pasar por revisión humana.

### 3.3. LLM generador de respuestas

Responde al usuario final, pero solo usando contexto recuperado desde:

- Neo4j;
- ChromaDB;
- conocimiento aprobado;
- permisos autorizados mediante RBAC.

Si no existe información suficiente, el asistente debe responder que no dispone de conocimiento validado para contestar.

---

## 4. Estado actual del desarrollo

El proyecto ya cuenta con una primera fase funcional de exploración estructural.

Actualmente se ha verificado que:

- el entorno Python funciona;
- las pruebas unitarias pasan correctamente;
- el crawler puede ejecutarse desde el perfil YAML;
- el sistema captura evidencias reales del ERP;
- se generan archivos estructurales procesados;
- se registran incertidumbres para revisión.

Resultado actual de pruebas:

```bash
59 passed
```

---

## 5. Estructura principal del proyecto

```text
.
├── app.py
├── configs
│   ├── cbmm.yaml
│   └── cbmm.legacy.yaml
├── data
│   ├── raw
│   │   ├── html
│   │   ├── playwright
│   │   └── screenshots
│   ├── processed
│   │   ├── structural
│   │   ├── semantic
│   │   └── visual
│   ├── review
│   │   ├── structural
│   │   ├── semantic
│   │   └── visual
│   ├── approved
│   │   ├── neo4j
│   │   └── chromadb
│   └── rejected
├── docker
│   └── neo4j
├── docs
├── scripts
│   ├── crawl_profile.py
│   └── inspect_login.py
├── src
│   ├── auth
│   ├── browser
│   ├── config
│   ├── crawler
│   ├── discovery
│   ├── extraction
│   ├── graph
│   ├── llm
│   ├── policy
│   ├── rag
│   ├── review
│   ├── semantic
│   └── storage
└── tests
```

---

## 6. Módulos principales

### Configuración

```text
src/config/profile_loader.py
```

Carga y valida perfiles YAML del crawler.

### Autenticación

```text
src/auth/auth_manager.py
```

Gestiona login y acceso al ERP.

### Navegación

```text
src/browser/navigator.py
```

Encapsula la navegación mediante Playwright.

### Políticas de ruta

```text
src/policy/route_policy.py
```

Controla qué rutas pueden o no explorarse.

### Crawler estructural

```text
src/crawler/route_crawler.py
src/crawler/frontier.py
src/crawler/state_signature.py
src/crawler/ui_event_explorer.py
```

Gestiona exploración, frontera de rutas, firmas de estados y eventos de interfaz.

### Descubrimiento de elementos

```text
src/discovery/link_discovery.py
src/discovery/menu_discovery.py
src/discovery/event_candidate_discovery.py
src/discovery/link_normalizer.py
```

Detecta enlaces, menús, eventos candidatos y normaliza rutas.

### Extracción de pantallas

```text
src/extraction/screen_extractor.py
```

Extrae texto visible, enlaces, botones, inputs, tablas e interactivos personalizados.

### Construcción estructural

```text
src/graph/screen_index_builder.py
src/graph/routes_graph_builder.py
```

Construye el índice estructural de pantallas y el grafo de navegación.

---

## 7. Archivos generados por el crawler

Al ejecutar el crawler se generan evidencias crudas en:

```text
data/raw/html/
data/raw/playwright/
data/raw/screenshots/
```

También se generan archivos procesados en:

```text
data/processed/structural/routes_graph.json
data/processed/structural/routes_graph.partial.json
data/processed/structural/screen_index.json
data/processed/structural/screen_index.partial.json
```

Además, las pantallas inciertas o errores de navegación se guardan en:

```text
data/review/structural/
```

---

## 8. Archivos estructurales principales

### screen_index.json

Archivo que contiene el índice detallado de pantallas descubiertas.

Incluye:

- ruta;
- URL;
- título;
- texto visible;
- enlaces;
- botones;
- campos;
- tablas;
- elementos interactivos personalizados;
- artefactos asociados;
- estado de descubrimiento;
- estado semántico.

Ejemplo de estado:

```json
{
  "status": "discovered",
  "knowledge_origin": "discovered",
  "semantic_status": "pending"
}
```

### routes_graph.json

Archivo que contiene el grafo preliminar de navegación.

Incluye:

- nodos;
- rutas;
- estados de interfaz;
- transiciones;
- eventos de clic;
- enlaces descubiertos;
- metadatos del crawler.

Ejemplos de relaciones:

```text
ui_event
ui_event_discovered_href
href_discovered
```

---

## 9. Comandos principales

### Entrar al proyecto

```bash
cd ~/Desktop/aprendizaje-chtb-cbmm
```

### Activar entorno virtual

```bash
source .venv/bin/activate
```

### Ejecutar pruebas

```bash
pytest
```

### Ejecutar crawler

Forma recomendada:

```bash
python -m scripts.crawl_profile --profile configs/cbmm.yaml --slow-mo 200
```

Modo headless:

```bash
python -m scripts.crawl_profile --profile configs/cbmm.yaml --headless
```

### Inspeccionar login

```bash
python -m scripts.inspect_login --profile configs/cbmm.yaml
```

### Ver archivos generados

```bash
find data -type f | sort
```

### Revisar carpetas de salida

```bash
ls -lah data/raw/html
ls -lah data/raw/playwright
ls -lah data/raw/screenshots
ls -lah data/processed/structural
ls -lah data/review/structural
```

---

## 10. Docker y Neo4j

Levantar Neo4j:

```bash
docker compose up -d neo4j
```

Ver estado:

```bash
docker compose ps
```

Ver logs:

```bash
docker compose logs -f neo4j
```

Detener Neo4j:

```bash
docker compose stop neo4j
```

Detener todos los servicios:

```bash
docker compose down
```

Acceso web:

```text
http://localhost:7474
```

Credenciales locales configuradas en `docker-compose.yml`:

```text
Usuario: neo4j
Contraseña: Admin123.
```

---

## 11. Ollama

Ver modelos instalados:

```bash
ollama list
```

Levantar servidor Ollama:

```bash
ollama serve
```

Descargar modelo generador:

```bash
ollama pull llama3.2
```

Descargar modelo para apoyo al crawler:

```bash
ollama pull qwen2.5-coder
```

Probar Ollama:

```bash
ollama run llama3.2 "Responde solo: Ollama funcionando"
```

Ver API local:

```bash
curl http://localhost:11434/api/tags
```

---

## 12. Flujo de desarrollo actual

El flujo actualmente confirmado es:

```text
1. Cargar perfil YAML.
2. Iniciar navegador con Playwright.
3. Autenticar en el ERP.
4. Navegar rutas permitidas.
5. Detectar enlaces, menús, botones y estados.
6. Capturar HTML, JSON y screenshots.
7. Construir screen_index.json.
8. Construir routes_graph.json.
9. Registrar incertidumbres en data/review/structural/.
```

---

## 13. Próximas fases de desarrollo

### Fase 1 — Normalización para Neo4j

Crear una capa que transforme:

```text
screen_index.json + routes_graph.json
```

en:

```text
graph_for_neo4j.json
```

Este archivo debe separar nodos y relaciones limpias para Neo4j.

Nodos esperados:

```text
ERP
Module
Screen
Route
UIState
Form
Field
Action
Table
Artifact
```

Relaciones esperadas:

```text
HAS_MODULE
HAS_SCREEN
HAS_ROUTE
HAS_UI_STATE
HAS_FIELD
HAS_ACTION
HAS_TABLE
HAS_ARTIFACT
NAVIGATES_TO
DISCOVERED_FROM
```

### Fase 2 — Importador Neo4j

Crear script para importar el grafo limpio a Neo4j.

Archivo sugerido:

```text
scripts/import_graph_to_neo4j.py
```

Módulo sugerido:

```text
src/graph/neo4j_importer.py
```

### Fase 3 — LLM semántico

Crear módulo para generar descripciones funcionales propuestas a partir de pantallas descubiertas.

Salida esperada:

```text
data/review/semantic/
```

### Fase 4 — Validación humana

Implementar mecanismo para aprobar, corregir o rechazar inferencias.

Estados sugeridos:

```text
pending
approved
rejected
needs_changes
```

### Fase 5 — Indexación en ChromaDB

Indexar únicamente conocimiento aprobado.

Salida esperada:

```text
data/approved/chromadb/
data/chroma_db/
```

### Fase 6 — Orquestador Graph RAG

Implementar recuperación híbrida:

```text
Consulta usuario
→ RBAC
→ Neo4j
→ ChromaDB
→ LLM generador
→ respuesta funcional
```

### Fase 7 — API y chat

Crear endpoints FastAPI para:

```text
/chat
/search
/screens
/review
/health
```

---

## 14. Reglas de seguridad del crawler

El crawler debe operar bajo reglas controladas.

Debe evitar:

- acciones destructivas;
- eliminar registros;
- guardar cambios reales;
- enviar formularios sensibles;
- confirmar procesos;
- ejecutar acciones de negocio sin autorización.

Acciones peligrosas deben enviarse a revisión antes de ejecutarse o descartarse.

---

## 15. Estado de conocimiento

Actualmente los elementos descubiertos tienen:

```text
knowledge_origin: discovered
semantic_status: pending
```

Esto significa que el crawler descubrió la estructura, pero todavía no existe conocimiento semántico aprobado.

La aprobación semántica será una fase posterior.

---

## 16. Buenas prácticas

No subir al repositorio:

```text
.env
contraseñas reales
tokens
credenciales del ERP
datos sensibles
docker/neo4j/data/
venv/
.venv/
__pycache__/
*.pyc
```

Crear ZIP seguro:

```bash
zip -r chat_cbmm_actual.zip . \
  -x "venv/*" ".venv/*" ".git/*" "__pycache__/*" "*.pyc" ".env" "docker/neo4j/data/*"
```

---

## 17. Comandos recomendados para iniciar cada jornada

```bash
cd ~/Desktop/aprendizaje-chtb-cbmm
source .venv/bin/activate
git status
pytest
docker compose up -d neo4j
```

Luego, si se va a ejecutar el crawler:

```bash
python -m scripts.crawl_profile --profile configs/cbmm.yaml --slow-mo 200
```

---

## 18. Estado resumido

```text
Ya implementado:
- carga de perfil YAML;
- login;
- navegación Playwright;
- políticas de ruta;
- exploración de enlaces y menús;
- detección de eventos UI;
- captura HTML/JSON/screenshot;
- screen_index.json;
- routes_graph.json;
- registro de incertidumbres;
- pruebas unitarias funcionales.

Pendiente:
- normalización para Neo4j;
- importación real a Neo4j;
- LLM Helper;
- LLM semántico;
- validación humana;
- indexación en ChromaDB;
- Graph RAG híbrido;
- RBAC en consulta;
- API/chat final.
```

---

## 19. Próximo paso inmediato

El siguiente paso técnico recomendado es construir el normalizador estructural:

```text
src/graph/neo4j_graph_normalizer.py
scripts/build_graph_for_neo4j.py
```

Objetivo:

```text
data/processed/structural/screen_index.json
data/processed/structural/routes_graph.json
        ↓
data/processed/structural/graph_for_neo4j.json
```

Ese archivo será la base para importar el conocimiento estructural del ERP a Neo4j.

## Backend FastAPI del asistente ERP (Fase 2B)

Esta fase expone una API determinista sobre `data/processed/structural/screen_index.json`. No usa LLM, embeddings, bases vectoriales, Neo4j ni ejecuta acciones en el ERP.

Terminal 1:

```bash
cd ~/Desktop/aprendizaje-chtb-cbmm
source .venv/bin/activate
python -m scripts.run_api
```

Terminal 2:

```bash
cd ~/Desktop/SiaCat/siacat_backend
yarn start:dev
```

Terminal 3:

```bash
cd ~/Desktop/SiaCat/siacat_frontend
npm run start:local
```

La configuración admite las variables opcionales `API_HOST`, `API_PORT`, `API_CORS_ORIGINS`, `SCREEN_INDEX_PATH`, `ROUTES_GRAPH_PATH`, `STATE_FLOW_GRAPH_PATH`, `SEARCH_MAX_RESULTS` y `SEARCH_MINIMUM_SCORE`. Las rutas relativas se resuelven desde la raíz del proyecto. CORS acepta por defecto únicamente `http://localhost:4200` y `http://127.0.0.1:4200`.

```bash
curl http://127.0.0.1:8000/api/health
```

```bash
curl -X POST http://127.0.0.1:8000/api/chat \\
  -H "Content-Type: application/json" \\
  -d '{
    "question": "¿Dónde consulto retenciones?",
    "conversationId": "prueba-local",
    "context": {
      "currentRoute": "/admin/home"
    }
  }'
```

OpenAPI está disponible localmente en `http://127.0.0.1:8000/api/docs`.
