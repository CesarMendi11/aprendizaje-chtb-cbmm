# Fase 1A — Base segura del crawler por estados

## Objetivo

Preparar el crawler existente para evolucionar hacia un `state-flow graph` inspirado en Crawljax, sin cambiar todavía el recorrido principal ni introducir restauración de estados.

## Cambios implementados

### 1. Modelos internos

Se añadieron modelos tipados y serializables:

- `UIEvent`
- `UIState`
- `Transition`
- `CrawlPath`
- `CrawlPathStep`

Estos modelos serán usados en la siguiente fase para reproducir trayectorias y representar transiciones reales entre estados.

### 2. Firma de estado v2

`StateSignatureBuilder` genera ahora:

- `exact_fingerprint`: detecta cualquier cambio observable normalizado.
- `structural_fingerprint`: identifica cambios funcionales e ignora datos volátiles.

La propiedad antigua `fingerprint` continúa disponible y apunta a la firma estructural para mantener compatibilidad.

La firma estructural puede ignorar:

- fechas y horas;
- UUID y tokens largos;
- identificadores numéricos volátiles;
- valores concretos de parámetros de consulta;
- cantidad de filas de una tabla.

Sí conserva cambios funcionales como:

- pestaña activa;
- menú expandido;
- nuevos enlaces;
- campos, botones y tablas;
- diálogos visibles.

### 3. Taxonomía de eventos

Los eventos ya no se tratan únicamente como `click`. Se clasifican en categorías como:

- `navigation_link`
- `expand_menu`
- `collapse_menu`
- `activate_tab`
- `open_modal`
- `open_dropdown`
- `submit_search`
- `change_pagination`
- `mutative_action`
- `unknown`

### 4. Política deny-by-default

La seguridad se separó del puntaje de descubrimiento.

El puntaje prioriza candidatos, pero no los autoriza. `EventPolicy` decide:

- `allow`: se puede explorar;
- `review`: requiere revisión y no se ejecuta;
- `deny`: está bloqueado.

Una acción desconocida o no incluida expresamente en el perfil no se ejecuta automáticamente.

### 5. Mejor extracción

El extractor captura también:

- `aria-expanded`;
- `aria-selected`;
- `aria-controls`;
- estado `disabled`;
- campos obligatorios y de solo lectura;
- combobox/listbox;
- diálogos visibles.

### 6. Auditoría sin abrir el ERP

Se añadió:

```bash
python -m scripts.audit_event_policy
```

El comando analiza el `screen_index.json` existente y genera:

```text
data/processed/structural/event_policy_audit.json
```

Así se puede revisar qué acciones serían permitidas, bloqueadas o enviadas a revisión antes de ejecutar nuevamente el crawler.

## Comprobación realizada sobre las evidencias existentes

Con las 11 pantallas actuales se identificaron:

- 278 candidatos permitidos;
- 19 candidatos para revisión;
- 6 candidatos bloqueados;
- 14 activaciones de pestaña;
- 24 acciones de paginación;
- 92 expansiones de menú;
- 140 enlaces de navegación;
- 4 desplegables;
- 4 búsquedas;
- 6 acciones mutativas.

Estos valores son una auditoría del conocimiento capturado anteriormente, no una nueva navegación del ERP.

## Próxima fase

La Fase 1B incorporará:

1. `StateRestorer`.
2. `PathReplayer`.
3. Exploración aislada de cada evento desde su estado fuente.
4. Registro correcto `UIState → UIEvent → UIState`.
5. Frontera de estados independiente de la frontera de rutas.
