# Consola de conocimiento CBMM

Primer corte demostrable de la consola administrativa independiente del ERP Angular (fases 6B.2B, 6B.2C y 6B.2D). Permite navegar ERP → módulo → pantalla y consultar el contexto read-only de una pantalla.

## Arquitectura

Aplicación cliente React + TypeScript + Vite, sin dependencias visuales ni estado global. `src/api` centraliza el origen de datos y el cliente HTTP; `src/data` contiene el snapshot; `src/features` separa árbol y detalle; `src/types` refleja los modelos Pydantic; CSS propio proporciona layout, estados y responsive.

## Modos de datos

- `demo` (predeterminado): usa un snapshot local tipado y no realiza solicitudes HTTP. La cabecera identifica “Modo demostración” y “Snapshot validado”. Este modo no prueba ni finge una conexión a PostgreSQL, FastAPI o la base real.
- `live`: consume exclusivamente FastAPI mediante la base relativa `/api`. Vite redirige esas solicitudes a `http://127.0.0.1:8000`. Un fallo se muestra claramente y nunca activa el snapshot de forma silenciosa.

Copie `.env.example` a `.env.local` y defina:

```env
VITE_ADMIN_API_MODE=demo
```

Valores admitidos: `demo` y `live`.

## Comandos

```bash
cd admin-ui
npm install
npm run dev
npm run typecheck
npm run build
```

Para la presentación, use `demo` y `npm run dev`; detenga el proceso al terminar. Para `live`, FastAPI debe estar disponible en `127.0.0.1:8000` y la API administrativa local debe estar habilitada por su configuración de backend.

## Endpoints live

- `GET /api/admin/knowledge-tree`
- `GET /api/admin/screens`
- `GET /api/admin/screens/{screen_id}/review-context`

El primer corte utiliza el árbol y el contexto. El listado de pantallas está tipado, pero no es necesario para la navegación jerárquica actual.

## Alcance y restricciones

La consola es provisional, local y estrictamente read-only. No incluye autenticación/RBAC definitivo ni acciones POST. Aprobar, corregir y rechazar quedan para la siguiente fase. El snapshot contiene únicamente el caso verificable de Retenciones; valores ausentes se presentan como no disponibles o colecciones vacías. La comparación demo conserva el resultado confirmado, pero no implica consulta en tiempo real. El cliente usa timeout, `AbortController`, errores sanitizados y validación de la frontera HTTP.
