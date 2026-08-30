# Consola de conocimiento CBMM

Consola administrativa React + TypeScript + Vite para operar y revisar el conocimiento gobernado de Chat-CBMM.

## Arquitectura

La Admin UI consume exclusivamente la API FastAPI mediante rutas relativas `/api`. Durante desarrollo, Vite redirige esas solicitudes al target configurado por `VITE_ADMIN_API_TARGET`. No existe modo demo ni snapshot local: si FastAPI no está disponible, la interfaz muestra el error correspondiente.

Copie `.env.example` a `.env.local` y configure, por ejemplo:

```env
VITE_ADMIN_API_TARGET=http://127.0.0.1:8000
```

El navegador continúa consumiendo rutas relativas `/api`; `VITE_ADMIN_API_TARGET` sólo configura el proxy del servidor de desarrollo de Vite.

## Comandos

```bash
cd admin-ui
npm install
npm run dev
npm run typecheck
npm run build
```

Para operar la consola, FastAPI debe estar disponible en el target configurado y la API administrativa local debe estar habilitada por la configuración del backend.

## Backend administrativo

La consola usa rutas `/api/admin/*` para estado del sistema, conocimiento estructural/semántico, pipeline, revisión, promoción y proyecciones.

## Alcance y seguridad

La identidad de revisor disponible en esta etapa es provisional y no equivale a autenticación/RBAC institucional. El cliente HTTP usa timeout, `AbortController`, errores sanitizados y validación de la frontera HTTP.
