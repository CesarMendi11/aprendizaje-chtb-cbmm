# Fase 1C.2 — Interacciones estables y reintentos controlados

## Problema observado

Después de restaurar `/admin/home`, algunos módulos del menú lateral eran localizados correctamente, pero Playwright agotaba el timeout mientras esperaba que el elemento terminara su animación y quedara estable. El selector no estaba perdido: el componente existía, era visible y funcionaba algunos segundos después.

Esto produjo una regresión aparente de cobertura:

- 19 rutas en la Fase 1C;
- 9 rutas en la primera ejecución de la Fase 1C.1;
- `General`, `Gerencial` y `Prevencion Riesgo` fallaron por `Locator.click: Timeout 1000ms exceeded`.

## Corrección

Se añadió `BrowserInteractionExecutor`, compartido por `UIEventExplorer` y `PathReplayer`.

El ejecutor:

1. espera que el elemento esté adjunto;
2. espera que sea visible;
3. lo desplaza al viewport;
4. espera un periodo breve de estabilización;
5. ejecuta un clic normal de Playwright;
6. reintenta de forma limitada si el componente sigue animándose.

Nunca usa `force=True` y no decide la seguridad de negocio. Solo recibe eventos previamente autorizados por la política `deny-by-default`.

## Configuración

```yaml
browser_interaction:
  click_timeout_ms: 3000
  click_attempts: 3
  retry_wait_ms: 500
  pre_click_wait_ms: 200
  scroll_into_view: true
```

## Diagnóstico guardado

Cada resultado de evento incorpora:

- `interaction_attempts`;
- `interaction_strategy`;
- `interaction_succeeded`;
- error de interacción cuando no se pudo hacer clic.

Las capturas de estados dinámicos usan el viewport en lugar de una captura `full_page`, reduciendo bloqueos por fuentes, animaciones y páginas extensas.

## Resultado esperado

La selección de candidatos de la Fase 1C.1 se conserva. La corrección solo evita falsos negativos causados por componentes todavía inestables después de restaurar una ruta.
