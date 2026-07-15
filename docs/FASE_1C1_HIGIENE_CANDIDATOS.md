# Fase 1C.1 — Higiene y presupuesto de eventos

## Objetivo

Reducir candidatos falsos o repetidos antes de habilitar la exploración
recursiva de estados.

## Problemas observados en el ERP real

- Selectores de tablas eran clasificados como pestañas porque `table` contiene
  la cadena `tab`.
- Botones repetidos en cada fila de una tabla generaban decenas de candidatos.
- Un enlace nativo y su representación como elemento personalizado aparecían
  duplicados.
- El menú global consumía el presupuesto de eventos en pantallas internas.
- Controles distintos con la misma etiqueta, como varios `mat-select` con
  `--Seleccione--`, podían fusionarse incorrectamente.
- Los calendarios no tenían una categoría funcional explícita.

## Cambios implementados

- Detección de pestañas por tokens y patrones semánticos, no por substring.
- Clasificación de controles de fila con `aria-expanded` como `expand_row`,
  pendiente de revisión humana.
- Deduplicación por plantilla de selector para acciones repetidas por fila.
- Deduplicación entre enlace nativo y elemento personalizado equivalente,
  prefiriendo el candidato con `href`.
- Conservación de controles iguales cuando están en regiones distintas o son
  dropdowns diferentes.
- Nueva categoría `open_date_picker`.
- Exclusión del menú global fuera de la pantalla raíz durante la ejecución.
- Presupuesto configurable por categoría.
- Auditoría del pipeline: crudos, deduplicados, elegibles, permitidos,
  seleccionados y razones de exclusión.

## Política conservadora actual

La búsqueda mediante formularios continúa deshabilitada en el presupuesto de
exploración (`submit_search: 0`). Las acciones desconocidas y las acciones por
fila no se ejecutan automáticamente.

## Resultado sobre la evidencia de la Fase 1C

Antes de esta fase:

- 2314 candidatos crudos.
- 670 candidatos permitidos en la auditoría anterior.
- 161 candidatos clasificados erróneamente como `activate_tab`.

Después de aplicar la nueva clasificación y deduplicación sobre las mismas 19
pantallas:

- 2314 candidatos crudos.
- 561 candidatos deduplicados.
- 488 candidatos elegibles.
- 388 candidatos permitidos antes del presupuesto.
- 23 eventos seleccionados para ejecución según región y categoría.
- 57 eventos enviados a revisión, frente a 197 antes de deduplicar acciones
  repetidas por fila.
- 0 falsos `activate_tab` derivados de la palabra `table`.

Estos valores corresponden a una auditoría offline sobre el `screen_index.json`
de la Fase 1C. Deben confirmarse con una nueva ejecución real después de aplicar
el parche.
