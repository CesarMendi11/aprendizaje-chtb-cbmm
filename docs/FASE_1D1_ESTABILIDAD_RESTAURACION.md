# Fase 1D.1 — Estabilidad y restauración reproducible

## Motivo

La primera ejecución real de profundidad 1 mantuvo las 19 rutas y registró 11
transiciones, pero 12 de los 23 eventos planificados no llegaron a ejecutarse.
La causa fue `state_restore_failed`, no una acción insegura ni un fallo del
selector.

Se observaron dos causas:

1. Algunas rutas se registraron usando el nombre descubierto en el menú, pero
   al volver directamente a la URL el resolvedor encontraba un encabezado
   distinto. Por ejemplo, `Facturación Electronica` frente a `Facturacion`.
2. Varias pantallas Angular todavía estaban cargando tablas o controles cuando
   se tomó la primera firma. Una extracción posterior encontraba una estructura
   más completa y la restauración la interpretaba como otro estado.

## Cambios

### Observación estable

`StableStateObserver` toma muestras sucesivas y solo considera estable una
pantalla cuando obtiene la misma firma estructural el número de veces definido
en el perfil y se cumple un tiempo mínimo de observación.

Se utiliza al:

- registrar un estado raíz;
- restaurar una ruta raíz;
- reproducir una trayectoria;
- capturar el estado posterior a un evento.

### Título canónico durante la restauración

El estado registrado conserva su título funcional. Cuando se restaura, ese
título se aplica como nombre canónico para que una variante del encabezado no
cambie artificialmente la identidad del estado. El título realmente observado
se conserva en `observed_functional_title`.

### Menú global limitado a rutas configuradas

La expansión del menú lateral forma parte de la firma únicamente en las rutas
incluidas en `state_detection.navigation_state_routes`. Para CBMM es solo
`/admin/home`. En pantallas internas, abrir o cerrar el sidebar no cambia la
identidad de la pantalla.

### Regiones sin conteos volátiles

La firma ya no guarda el número exacto de elementos de una región. Conserva
únicamente si la región está presente. Así, una tabla que agrega botones al
terminar de cargar no cambia la firma solo por el conteo.

### Auditoría real de ejecución

El comando:

```bash
python -m scripts.inspect_ui_event_results
```

resume el resultado más reciente por pantalla y diferencia:

- `changed`;
- `unchanged`;
- `restore_failed`;
- `interaction_failed`;
- `execution_error`.

También conserva intentos de interacción, estrategia de restauración y
diagnóstico de estabilidad.

## Configuración CBMM

```yaml
state_detection:
  navigation_state_routes:
    - "/admin/home"
  stability:
    enabled: true
    timeout_ms: 4000
    interval_ms: 250
    minimum_observation_ms: 500
    required_consecutive_samples: 2
```

## Criterio de validación

La fase queda validada cuando:

- se mantienen las 19 rutas raíz;
- los 23 eventos del plan aparecen en la auditoría;
- no existen `state_restore_failed` causados por títulos o carga asíncrona;
- las acciones mutativas permanecen bloqueadas;
- los calendarios y desplegables seguros se intentan realmente;
- `max_event_depth` continúa en 1.
