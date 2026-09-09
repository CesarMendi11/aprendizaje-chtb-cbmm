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

Regla de independencia: primero se congela esta referencia humana; después se ejecuta el crawl y se compara con `scripts.experiments.evaluate_structural`.
