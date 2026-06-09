# Comandos útiles — Proyecto Chat CBMM Graph RAG ERP

Proyecto: `aprendizaje-chtb-cbmm`  
Ruta local usada:

```bash
cd ~/Desktop/aprendizaje-chtb-cbmm
```

---

## 1. Verificar estado general del proyecto

```bash
pwd
ls
tree -L 4
git status
git log --oneline -5
```

Si no tienes `tree`:

```bash
find . -maxdepth 4 -type f
```

---

## 2. Crear y activar entorno virtual Python

Crear entorno virtual:

```bash
python3 -m venv .venv
```

Activar entorno virtual:

```bash
source .venv/bin/activate
```

Verificar Python activo:

```bash
which python
python --version
pip --version
```

Desactivar entorno virtual:

```bash
deactivate
```

---

## 3. Instalar dependencias del proyecto

Con el entorno virtual activado:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Instalar navegador Chromium de Playwright:

```bash
python -m playwright install chromium
```

Instalar todos los navegadores de Playwright:

```bash
python -m playwright install
```

---

## 4. Ejecutar pruebas

Ejecutar todas las pruebas:

```bash
pytest
```

Ejecutar con más detalle:

```bash
pytest -v
```

Ejecutar una prueba específica:

```bash
pytest tests/test_profile_loader.py -v
pytest tests/test_route_policy.py -v
pytest tests/test_route_crawler.py -v
pytest tests/test_screen_extractor.py -v
```

Detener al primer error:

```bash
pytest -x
```

---

## 5. Revisar calidad del código

Formatear código con Black:

```bash
black src scripts tests
```

Revisar código con Ruff:

```bash
ruff check src scripts tests
```

Corregir automáticamente con Ruff cuando sea posible:

```bash
ruff check src scripts tests --fix
```

---

## 6. Docker y Neo4j

Levantar Neo4j:

```bash
docker compose up -d neo4j
```

Ver contenedores activos:

```bash
docker compose ps
```

Ver logs de Neo4j:

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

Reiniciar Neo4j:

```bash
docker compose restart neo4j
```

Abrir Neo4j en navegador:

```text
http://localhost:7474
```

Credenciales actuales según `docker-compose.yml`:

```text
Usuario: neo4j
Contraseña: Admin123.
```

Entrar a Cypher Shell dentro del contenedor:

```bash
docker exec -it cbmm_neo4j cypher-shell -u neo4j -p 'Admin123.'
```

Probar conexión en Cypher:

```cypher
RETURN 1 AS ok;
```

Ver nodos existentes:

```cypher
MATCH (n) RETURN labels(n), count(n);
```

Borrar todo el grafo, solo si estás seguro:

```cypher
MATCH (n) DETACH DELETE n;
```

---

## 7. Inspeccionar login del ERP

Abrir navegador y guardar HTML/screenshot del login:

```bash
python scripts/inspect_login.py --profile configs/cbmm.yaml
```

Con pausa lenta entre acciones:

```bash
python scripts/inspect_login.py --profile configs/cbmm.yaml --slow-mo 300
```

Archivos generados:

```text
data/cache/login_debug.html
data/cache/login_debug.png
```

---

## 8. Ejecutar crawler estructural del ERP

Ejecutar crawler con navegador visible:

```bash
python scripts/crawl_profile.py --profile configs/cbmm.yaml
```

Ejecutar con navegador visible y acciones lentas:

```bash
python scripts/crawl_profile.py --profile configs/cbmm.yaml --slow-mo 200
```

Ejecutar en modo oculto:

```bash
python scripts/crawl_profile.py --profile configs/cbmm.yaml --headless
```

Ejecutar mediante `app.py`:

```bash
python app.py
```

---

## 9. Revisar evidencias generadas por el crawler

Contar HTML capturados:

```bash
find data/raw/html -type f -name "*.html" | wc -l
```

Contar JSON de Playwright:

```bash
find data/raw/playwright -type f -name "*.json" | wc -l
```

Contar capturas de pantalla:

```bash
find data/raw/screenshots -type f -name "*.png" | wc -l
```

Listar evidencias capturadas:

```bash
ls data/raw/html
ls data/raw/playwright
ls data/raw/screenshots
```

Ver últimos archivos generados:

```bash
find data/raw -type f -printf "%TY-%Tm-%Td %TH:%TM %p\n" | sort | tail -30
```

---

## 10. Carpetas importantes del proyecto

Código fuente:

```text
src/
```

Perfiles YAML:

```text
configs/cbmm.yaml
configs/cbmm.legacy.yaml
```

Scripts ejecutables:

```text
scripts/crawl_profile.py
scripts/inspect_login.py
```

Evidencias crudas:

```text
data/raw/html/
data/raw/playwright/
data/raw/screenshots/
```

Procesamiento estructural:

```text
data/processed/structural/
```

Procesamiento semántico:

```text
data/processed/semantic/
```

Revisión humana:

```text
data/review/
```

Conocimiento aprobado:

```text
data/approved/
```

Conocimiento rechazado:

```text
data/rejected/
```

---

## 11. Módulos principales del código

Carga de perfiles YAML:

```text
src/config/profile_loader.py
```

Login/autenticación:

```text
src/auth/auth_manager.py
```

Navegación Playwright:

```text
src/browser/navigator.py
```

Políticas de rutas:

```text
src/policy/route_policy.py
```

Crawler estructural:

```text
src/crawler/route_crawler.py
src/crawler/frontier.py
src/crawler/state_signature.py
src/crawler/ui_event_explorer.py
```

Descubrimiento de interfaz:

```text
src/discovery/link_discovery.py
src/discovery/menu_discovery.py
src/discovery/event_candidate_discovery.py
src/discovery/link_normalizer.py
```

Extracción de pantallas:

```text
src/extraction/screen_extractor.py
```

Construcción estructural:

```text
src/graph/screen_index_builder.py
src/graph/routes_graph_builder.py
```

Pendientes principales:

```text
src/llm/
src/semantic/
src/rag/
src/api/
src/chatbot/
```

---

## 12. Ollama y modelos locales

Ver modelos instalados:

```bash
ollama list
```

Levantar Ollama si no está activo:

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

Probar modelo:

```bash
ollama run llama3.2 "Responde solo: Ollama funcionando"
```

Verificar API local de Ollama:

```bash
curl http://localhost:11434/api/tags
```

Funciones previstas de LLM en el proyecto:

```text
1. LLM Helper:
   propone reglas para incertidumbres del crawler.

2. LLM semántico:
   genera descripciones funcionales propuestas.

3. LLM generador:
   responde al usuario usando contexto recuperado por Graph RAG.
```

---

## 13. ChromaDB

Carpeta actual para base vectorial:

```text
data/chroma_db/
```

Carpeta prevista para conocimiento aprobado:

```text
data/approved/chromadb/
```

Verificar que ChromaDB esté instalado:

```bash
python -c "import chromadb; print('ChromaDB OK')"
```

---

## 14. Comandos Git útiles

Ver estado:

```bash
git status
```

Ver cambios:

```bash
git diff
```

Agregar cambios:

```bash
git add .
```

Crear commit:

```bash
git commit -m "avance crawler estructural"
```

Subir cambios:

```bash
git push
```

Traer cambios:

```bash
git pull
```

Ver historial breve:

```bash
git log --oneline --graph --decorate -10
```

---

## 15. Limpiar archivos temporales

Eliminar `__pycache__`:

```bash
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
```

Eliminar archivos `.pyc`:

```bash
find . -name "*.pyc" -delete
```

Limpiar caché de pruebas:

```bash
rm -rf .pytest_cache
```

No borrar estas carpetas sin respaldo:

```text
data/raw/
data/processed/
docker/neo4j/data/
```

---

## 16. Crear ZIP seguro del proyecto

ZIP recomendado para compartir con ChatGPT o respaldo, excluyendo entorno virtual, Git, caché, `.env` y base pesada de Neo4j:

```bash
zip -r chat_cbmm_actual.zip . \
  -x "venv/*" ".venv/*" ".git/*" "__pycache__/*" "*.pyc" ".env" "docker/neo4j/data/*"
```

ZIP incluyendo evidencias, pero sin Neo4j pesado:

```bash
zip -r chat_cbmm_con_evidencias.zip . \
  -x "venv/*" ".venv/*" ".git/*" "__pycache__/*" "*.pyc" ".env" "docker/neo4j/data/*"
```

---

## 17. Ver tamaño de carpetas

Ver tamaño general:

```bash
du -sh .
```

Ver tamaño por carpeta:

```bash
du -h --max-depth=2 | sort -h
```

Ver carpetas más pesadas:

```bash
du -h --max-depth=3 | sort -hr | head -20
```

---

## 18. Ruta técnica actual del desarrollo

Estado actual:

```text
Crawler estructural base avanzado.
Evidencias reales capturadas del ERP.
Falta consolidar procesamiento estructural, Neo4j, LLM semántico, validación, ChromaDB y Graph RAG.
```

Siguiente ruta recomendada:

```text
1. Validar pruebas con pytest.
2. Revisar configs/cbmm.yaml.
3. Consolidar data/processed/structural/screen_index.json.
4. Consolidar data/processed/structural/routes_graph.json.
5. Importar estructura a Neo4j.
6. Crear LLM semántico para descripciones propuestas.
7. Guardar inferencias en data/review/semantic/.
8. Crear flujo de aprobación humana.
9. Indexar conocimiento aprobado en ChromaDB.
10. Crear orquestador Graph RAG.
11. Crear endpoint /chat.
12. Aplicar RBAC antes de entregar contexto al LLM.
```

---

## 19. Comandos iniciales recomendados cada día

```bash
cd ~/Desktop/aprendizaje-chtb-cbmm
source .venv/bin/activate
git status
docker compose up -d neo4j
pytest
```

Luego, según lo que se vaya a trabajar:

```bash
python scripts/crawl_profile.py --profile configs/cbmm.yaml --slow-mo 200
```

O:

```bash
python scripts/inspect_login.py --profile configs/cbmm.yaml
```

---

## 20. Recordatorio de alcance

El proyecto actual se enfoca en:

```text
Soporte funcional del ERP:
- módulos
- pantallas
- rutas
- formularios
- botones
- acciones
- navegación
- orientación funcional
```

Fuera del alcance actual:

```text
- tickets
- impresoras
- hardware
- internet
- sistema operativo
- soporte técnico general
- escalamiento formal a mesa de ayuda
```

---

## Comando rápido de arranque

```bash
cd ~/Desktop/aprendizaje-chtb-cbmm
source .venv/bin/activate
docker compose up -d neo4j
pytest
```
