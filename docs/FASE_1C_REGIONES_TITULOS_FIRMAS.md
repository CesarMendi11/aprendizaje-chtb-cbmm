# Fase 1C — Regiones, títulos funcionales y firma estructural v3

## Objetivo

Reducir ruido del layout global antes de habilitar exploración recursiva de estados.

## Cambios

- Clasificación genérica de elementos en `global_navigation`, `header`,
  `main_content`, `dialog`, `footer` y `volatile`.
- Selectores configurables por perfil ERP.
- Resolución de título funcional mediante encabezados, breadcrumbs, pista del
  enlace observado, navegación activa, ruta y `document.title` como respaldo.
- Firma estructural basada en contenido local, diálogos y estado resumido del
  menú global.
- Priorización de eventos locales fuera de la ruta principal.
- Eliminación de relaciones repetidas causadas por el menú lateral persistente.
- Regeneración automática de `event_policy_audit.json` al terminar un crawl.

## Compatibilidad

Los campos anteriores se conservan. Los JSON antiguos sin `region` siguen
siendo procesables y se interpretan como contenido principal.

## Validación recomendada

```bash
pytest
python -m scripts.crawl_profile --profile configs/cbmm.yaml --slow-mo 200
python -m scripts.inspect_screen_quality
python -m scripts.inspect_state_flow
```

La profundidad dinámica permanece en cero durante esta fase. Primero se revisa
la calidad de títulos, regiones y firmas; luego se habilita `StateFrontier`.
