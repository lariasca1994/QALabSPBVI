# Informe de validación local

**Generado:** 2026-10-03T22:43:39-05:00  
**Alcance:** backend FastAPI y frontend React/Vite

## Resumen

| Paso | Estado | Código de salida | Resultado |
|---|---|---:|---|
| Compilación frontend | PASS | 0 | `npm run build`; TypeScript y Vite finalizaron correctamente. |
| Pruebas backend | PASS | 0 | `python -m pytest -q`; 72 pruebas aprobadas. |
| Pruebas E2E | PASS | 0 | `npm run test:e2e`; 4 recorridos aprobados con Chromium. |
| Inicio para E2E | PASS | 0 | FastAPI respondió en `http://127.0.0.1:8010/health` y Vite en `http://127.0.0.1:5174`. |
| Limpieza | PASS | 0 | No quedaron directorios temporales E2E ni procesos escuchando en los puertos 8010 y 5174. |

**Resultado general: PASS**, con la limitación de infraestructura indicada abajo.

## Entorno y cobertura

- Node.js: `v24.19.0`.
- Python: `3.13.14`.
- Playwright: `1.63.0`; Chromium ejecutó las pruebas en modo headless.
- Docker: no disponible (`docker info` terminó con código 1). El runner de E2E no dependió de Docker.
- Los recorridos E2E usaron bases SQLite temporales aisladas y el Mongo simulado del servidor de prueba; el runner limpió esos datos al terminar.
- Se probaron recorridos de autenticación MFA y ejecución QA, administración del ciclo de vida de llaves, pago intra-SPBVI idempotente y pago inter-SPBVI con MOL local y mensajes ISO.
- Se verificó el diseño móvil en anchos de 320 y 375 px, incluido el desbordamiento horizontal.

## Evidencia

- `npm run build` — salida 0.
- `python -m pytest -q` — 72 aprobadas, salida 0.
- `npm run test:e2e` — 4 aprobadas, salida 0.
- La respuesta del pago inter-SPBVI informa `completed`; la aserción E2E quedó alineada con ese contrato.

## Limitación

Esta validación no sustituye una ejecución contra PostgreSQL y MongoDB reales. La fidelidad de infraestructura queda pendiente hasta habilitar Docker; las pruebas de navegador sí se ejecutaron y no se omitieron.
