# Fase 1B — Restauración y reproducción de estados

## Objetivo

Evitar que los eventos de interfaz se exploren de forma acumulativa.

Antes de esta fase, el crawler podía ejecutar una secuencia como:

```text
estado inicial
→ abre menú A
→ desde menú A abierto prueba menú B
```

Eso podía atribuir la segunda transición al estado inicial aunque realmente
partiera de una interfaz ya modificada.

La Fase 1B implementa:

```text
estado fuente S1
→ restaurar S1
→ ejecutar evento E1
→ registrar S2
→ restaurar S1
→ ejecutar evento E2
→ registrar S3
```

## Componentes

### `StateRegistry`

- Deduplica estados por firma estructural.
- Conserva firmas exactas observadas.
- Mantiene la trayectoria reproducible más corta.

### `StateFrontier`

- Cola estados, no solo URLs.
- Permite distinguir múltiples estados en la misma ruta.
- Queda preparada para la exploración recursiva de la siguiente fase.

### `PathReplayer`

- Navega al estado raíz.
- Reproduce eventos previamente autorizados.
- Verifica la firma estructural después de cada paso.
- Rechaza eventos `review` o `deny`.

### `StateRestorer`

- Detecta si el navegador ya está en el estado esperado.
- Restaura estados raíz mediante navegación directa.
- Restaura estados dinámicos mediante reproducción de trayectoria.

### `StateFlowGraphBuilder`

Produce un grafo separado del grafo legado de rutas:

```text
data/processed/structural/state_flow_graph.json
```

Incluye:

- estados raíz;
- estados dinámicos;
- eventos;
- transiciones observadas;
- trayectorias reproducibles;
- estrategia de restauración utilizada.

También se genera:

```text
data/processed/structural/state_registry.json
```

## Compatibilidad

`routes_graph.json` y `screen_index.json` se siguen generando. Los nodos de
estado dentro de `routes_graph.json` conservan el identificador legado
`ruta#state:firma` para no romper consumidores existentes.

El nuevo `state_flow_graph.json` utiliza identificadores canónicos basados en la
firma estructural completa.

## Seguridad

- Solo se reproducen eventos con decisión `allow`.
- Los eventos `review` y `deny` nunca se ejecutan mediante `PathReplayer`.
- Si no se puede restaurar un estado, el candidato se registra como error y no
  se prueba desde una interfaz incierta.
- Las capturas de eventos tienen timeout propio; un fallo de screenshot no
  convierte una transición correcta en fallo de navegación.

## Configuración

```yaml
state_replay:
  enabled: true
  page_wait_ms: 800
  step_wait_ms: 400
  click_timeout_ms: 1000
  verify_each_step: true
  restore_attempts: 2

ui_events:
  restore_after_exploration: true
  capture_event_artifacts: true
  artifact_timeout_ms: 3000
```

## Validación

```bash
pytest
```

Después de una ejecución controlada del crawler:

```bash
python -m scripts.inspect_state_flow
```

## Alcance de esta entrega

Esta fase aísla correctamente los eventos de las pantallas que el crawler ya
explora. La exploración recursiva de estados dinámicos mediante `StateFrontier`
se habilitará después de revisar el primer `state_flow_graph.json` generado
contra el ERP real.
