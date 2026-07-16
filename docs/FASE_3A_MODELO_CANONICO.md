# Fase 3A: modelo canónico de conocimiento

## Propósito

El modelo canónico convierte observaciones estructurales del crawler en un documento estable, tipado, validable y trazable. Un artefacto crudo describe lo que una ejecución observó; el conocimiento canónico normaliza identidades, relaciones, seguridad, procedencia y estado de revisión sin depender del framework de interfaz ni de una base de datos.

Esta fase no reemplaza `StructuralKnowledgeRepository`: FastAPI y `/api/chat` siguen leyendo `screen_index.json`.

## Documento y entidades

`CanonicalKnowledgeBase` contiene versión de esquema y conocimiento, identidad del ERP, fuentes y hashes, estadísticas y las entidades `Module`, `Screen`, `UIState`, `Field`, `Control`, `Table`, `TableColumn`, `Link`, `Event`, `Transition` y `Evidence`. Las relaciones usan IDs, no objetos anidados.

`Control` representa botones, enlaces, pestañas, menús, desplegables, paginación y otros controles. Así se pueden consultar botones sin asumir que todas las acciones son elementos HTML `button`.

## Identidad, versiones y revisión

Los IDs derivados usan SHA-256 truncado sobre identidad funcional normalizada: ERP, tipo, ruta, relación padre, etiqueta y posición solo donde diferencia repeticiones. Nunca incorporan fechas ni datos volátiles.

`knowledge_version` es un hash del contenido funcional y permanece estable ante ejecuciones equivalentes. `generated_at` registra el instante de construcción. Todo conocimiento automático nace como `pending_review`; también existen `approved`, `rejected` y `corrected`, con campos futuros de auditoría humana.

## Evidencia y privacidad

La evidencia referencia rutas y hashes de JSON, HTML o capturas originales; no copia HTML, imágenes ni cuerpos de red. El constructor excluye regiones sensibles o volátiles y filtra defensivamente correo, IPv4, IPv6, tokens, secretos y fechas/horas volátiles. Los reportes solo conservan conteos agregados.

La inferencia de módulos usa evidencia del grafo, eventos de expansión y relaciones observadas. Si no hay evidencia suficiente, `module_id` queda nulo y se genera una advertencia.

## Archivos generados

- `data/processed/canonical/knowledge.json`: documento completo.
- `data/processed/canonical/manifest.json`: versiones, hashes y conteos.
- `data/processed/canonical/build_report.json`: advertencias, omisiones, referencias, duplicados y exclusiones.

## Comandos

```bash
python -m scripts.build_canonical_knowledge --profile configs/cbmm.yaml
python -m scripts.inspect_canonical_knowledge
python -m scripts.validate_canonical_knowledge
```

El constructor admite `--output-dir`, `--strict`, `--pretty` y `--no-pretty`. Las rutas se resuelven desde la raíz del proyecto.

## Preparación para persistencia

Los modelos tipados y referencias por ID permiten mapear posteriormente entidades relacionales a PostgreSQL, relaciones y transiciones a Neo4j, y fragmentos revisados a ChromaDB. La evidencia y `review_status` permiten limitar las cargas futuras a contenido trazable y aprobado.

## Fase 3B

La siguiente fase podrá definir persistencia, migraciones y sincronización incremental. PostgreSQL, SQLAlchemy, Alembic, Neo4j, ChromaDB, embeddings, LLM y Graph RAG quedan deliberadamente fuera de 3A.
