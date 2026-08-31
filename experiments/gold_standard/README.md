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
- `name`: nombre visible del módulo/submódulo o título de pantalla;
- `route`: obligatoria para `screen`; vacía para `module` si no existe una ruta estable propia;
- `notes`: observaciones del revisor, no utilizadas para el matching.

Ejemplos ilustrativos de formato (no deben copiarse como datos reales sin verificarlos manualmente):

```csv
entity_type,parent_module_path,name,route,notes
module,,General,,
module,Trámites,Rastrear,,
screen,General,Año,/admin/general/anios,
screen,Trámites > Rastrear,Rastrear externos,/admin/tramites/rastrear/externos,
```

Regla de independencia: primero se congela esta referencia humana; después se ejecuta el crawl y se compara con `scripts.experiments.evaluate_structural`.
