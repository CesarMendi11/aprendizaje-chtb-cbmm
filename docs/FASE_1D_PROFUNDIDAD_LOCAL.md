# Fase 1D — Profundidad local controlada

## Objetivo

Separar definitivamente la profundidad de rutas de la profundidad de estados UI y habilitar una primera exploración local segura en las pantallas raíz del ERP.

La navegación principal del Dashboard continúa descubriendo módulos mediante `expand_menu`, mientras que las pantallas internas solo prueban categorías locales explícitamente autorizadas.

## Semántica de profundidad

- `max_event_depth: 0`: conserva únicamente la expansión de módulos del Dashboard para descubrir rutas.
- `max_event_depth: 1`: además explora controles locales de cada pantalla raíz y registra estados de profundidad 1.
- `max_event_depth: 2`: reproduce estados de profundidad 1 mediante `PathReplayer` y explora desde ellos para descubrir estados de profundidad 2.

La profundidad de rutas (`exploration.max_depth`) y la profundidad de eventos (`ui_events.max_event_depth`) son límites independientes.

## Categorías habilitadas

En el Dashboard:

- `expand_menu`

En pantallas internas:

- `activate_tab`
- `open_readonly_view`
- `open_date_picker`
- `open_modal`
- `open_dropdown`
- `change_pagination`

Las búsquedas continúan deshabilitadas mediante presupuesto `submit_search: 0`. Las acciones mutativas permanecen en `deny`.

## StateFrontier

Los estados dinámicos nuevos se agregan a `StateFrontier` únicamente cuando su profundidad es menor a `max_event_depth`. Antes de explorar un estado pendiente:

1. `StateRestorer` restaura la trayectoria.
2. Se verifica la firma estructural esperada.
3. Se descubren candidatos locales permitidos.
4. Cada evento se ejecuta de manera aislada.
5. Se registra la transición en `state_flow_graph.json`.

## Inspección previa sin navegador

Antes de ejecutar el crawler real se puede revisar el plan calculado sobre el `screen_index.json` actual:

```bash
python -m scripts.inspect_event_plan
```

El comando no abre el ERP ni ejecuta clics. Genera `event_execution_plan.json` y muestra ruta, categoría, etiqueta y región de cada evento que se intentaría explorar.

## Archivos adicionales

- `data/processed/structural/event_execution_plan.json`
- `data/processed/structural/state_exploration_summary.json`
- `data/processed/structural/state_exploration_summary.partial.json`

El resumen registra configuración, estados fuente explorados y estados pendientes.

## Seguridad

Esta fase no habilita:

- envío de formularios;
- creación o modificación de registros;
- selección automática de opciones de negocio;
- clics `force=True`;
- categorías desconocidas;
- eventos clasificados como `review` o `deny`.

## Validación

Se añadieron pruebas para comprobar:

- compatibilidad de profundidad cero;
- exploración local en profundidad uno;
- reproducción recursiva en profundidad dos;
- filtrado explícito por categorías;
- validación del perfil YAML.
