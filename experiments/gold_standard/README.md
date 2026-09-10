# Gold Standards

Las referencias de esta carpeta deben construirse independientemente de los resultados que produzca el nuevo crawl experimental.

Plan inicial:

- censo completo: módulos, submódulos, pantallas, rutas y jerarquía principal;
- muestra estratificada: campos, controles, tablas, columnas, estados, eventos y transiciones;
- Gold Standard conversacional: hechos atómicos obligatorios y comportamiento esperado (responder, aclarar o abstenerse).

## `structural_reference.csv`

Este archivo es la referencia humana previa al `CRAWL FULL`. No debe llenarse copiando salidas del crawler, del modelo canónico, de Neo4j, de ChromaDB ni de snapshots históricos.

Columnas:

- `entity_type`: `module` o `screen`;
- `parent_module_path`: jerarquía humana separada por ` > `; vacía para módulos raíz;
- `name`: para `module`, nombre visible del módulo/submódulo; para `screen`, el título o encabezado visible **dentro de la pantalla una vez abierta**, no la etiqueta del menú usada para llegar a ella;
- `route`: obligatoria para `screen`; vacía para `module` si no existe una ruta estable propia;
- `notes`: observaciones del revisor, no utilizadas para el matching. Si la etiqueta del menú difiere del título interno de una pantalla, puede registrarse aquí como `menu_label: ...`.

Ejemplos ilustrativos de formato (no deben copiarse como datos reales sin verificarlos manualmente):

```csv
entity_type,parent_module_path,name,route,notes
module,,General,,
module,Trámites,Rastrear,,
screen,General,Año,/admin/general/anios,
screen,Trámites > Rastrear,Rastrear externos,/admin/tramites/rastrear/externos,
```

### Regla de identidad para pantallas

La dimensión primaria `screen` conserva el contrato congelado `route + name`, y `screen_hierarchy` conserva `route + parent_module_path + name`. Para evitar confundir dos textos distintos del ERP, el `name` de una fila `screen` debe corresponder al **título/encabezado visible dentro de la pantalla**, observado después de abrirla. La etiqueta del menú no sustituye ese título.

El evaluador también reporta `screen_route` y coincidencias condicionales de título/jerarquía como **diagnósticos interpretativos**. No reemplazan ni recalculan oportunistamente las dimensiones primarias de RQ1.

### Casos de interfaz irregular

La referencia debe conservar lo observado en el ERP, incluso cuando la interfaz sea inconsistente.

- Si una pantalla funcional accesible no presenta un título/encabezado de página visible e inequívoco, deje `name` vacío y escriba `title_status: absent` en `notes`. No sustituya la ausencia con la etiqueta del menú. La pantalla permanece en el censo primario y `screen_route` permite separar detección de ruta de discordancia de título.
- Si un destino de navegación abre únicamente la página genérica 404/no encontrada durante la inspección independiente, no lo registre como `screen` funcional en `structural_reference.csv`. Regístrelo en `structural_navigation_anomalies.csv`. Por tanto, no se convierte automáticamente en FN. Si el sistema FORMAL materializa de todas formas una pantalla funcional en esa ruta, esa sobre-detección no se enmascara automáticamente.
- Para RQ1, `screen` significa una **vista funcional estable y direccionable por ruta** expuesta como destino de navegación. Variantes de ruta estables del menú que reutilizan el mismo componente frontend se registran por separado cuando el parámetro cambia el contexto funcional mostrado. Esto no significa que sean componentes Angular distintos.
- IDs transitorios de registros individuales o rutas generadas ad hoc que no constituyan destinos estables del menú no deben inflar el censo.
- Las jerarquías deben usar el path completo (`Módulo > Submódulo`) y no solo el nombre del submódulo inmediato.

Estas reglas fueron fijadas PRE-FORMAL a partir de la inspección humana independiente, antes de observar cualquier salida FORMAL del crawler y sin modificar el runtime M1/M2/M3.

Regla de independencia: primero se congela esta referencia humana; después se ejecuta el crawl y se compara con `scripts.experiments.evaluate_structural`.
