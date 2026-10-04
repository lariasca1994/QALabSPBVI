# QALabSPBVI

Laboratorio simulado de pagos inmediatos interoperables, inspirado en Bre-B (Banco de la República de Colombia), para practicar y mostrar aseguramiento de calidad automatizado: pruebas de API REST/JSON, mensajería ISO 20022 de laboratorio y recorridos E2E. Es un proyecto de portafolio: **no se conecta a ninguna infraestructura real de pagos** y el MOL es una simulación.

- Laboratorio en línea: <https://azfdeys6cijfwmjqvi1-hvgvfchjh7htgmf7.z02.azurefd.net> (requiere una cuenta creada por un administrador).
- Documentación interactiva de la API: `/api/docs` en el laboratorio o `http://127.0.0.1:8000/docs` en local.

## Contenido

1. [Arquitectura](#arquitectura)
2. [Puesta en marcha local](#puesta-en-marcha-local)
3. [Acceso seguro y roles](#acceso-seguro-y-roles)
4. [Llaves Bre-B (DIFE y DICE)](#llaves-bre-b-dife-y-dice)
5. [Cuentas y pagos](#cuentas-y-pagos)
6. [Gestión QA](#gestión-qa)
7. [Programa de pruebas ISO 20022 Bre-B](#programa-de-pruebas-iso-20022-bre-b)
8. [Correos transaccionales](#correos-transaccionales)
9. [Interfaz web](#interfaz-web)
10. [Pruebas automatizadas](#pruebas-automatizadas)
11. [CI/CD y laboratorio en la nube](#cicd-y-laboratorio-en-la-nube)
12. [Estructura del repositorio](#estructura-del-repositorio)
13. [Supuestos y pendientes](#supuestos-y-pendientes)

## Arquitectura

Monolito modular en Python (FastAPI + SQLAlchemy 2) con dominios separados y listos para volverse servicios, más una interfaz React/TypeScript. Cada componente usa su propia base de datos, nueva y dedicada; ninguna se comparte ni reutiliza datos de otros proyectos.

| Dominio | Responsabilidad | Persistencia |
|---|---|---|
| Pagos y autenticación | Cuentas, ledger, pagos intra/inter-SPBVI, usuarios, sesiones y MFA | PostgreSQL (`DATABASE_URL`) |
| DIFE | Directorio federado de llaves de cada SPBVI y auditoría del ciclo de vida | SQL Server (`DIFE_DATABASE_URL`) |
| DICE | Directorio centralizado: índice global de llaves para el enrutamiento inter-SPBVI | Oracle (`DICE_DATABASE_URL`) |
| QA | Épicas, HU, CP, tareas, ejecuciones, bugs, fixes y avisos | MongoDB (`MONGODB_URL`, `MONGODB_DATABASE`) |
| ISO 20022 | Adaptador que genera y valida `pacs.008` / `pacs.002` de laboratorio | Sin persistencia |
| MOL simulado | Liquidación inter-SPBVI en una transacción local | Usa la base de pagos |

El núcleo del dominio no depende del formato de mensaje: el adaptador ISO 20022 recibe datos neutrales del pago. Todos los montos son enteros en centavos.

## Puesta en marcha local

### Requisitos

- Python 3.11 o superior y Node.js 20 o superior.
- Bases locales nuevas y dedicadas: PostgreSQL, SQL Server (con ODBC Driver 18), Oracle (p. ej. Oracle Free con `FREEPDB1`) y MongoDB. Sin PostgreSQL se puede usar SQLite temporal para pagos y autenticación.
- Una cuenta de Brevo para enviar el código MFA y los avisos.

### Configuración

Copia `.env.example` a `.env` y completa sus valores. Los nombres deben coincidir exactamente: la configuración ignora variables desconocidas, así que un nombre distinto deja la conexión en su valor por defecto sin avisar.

| Variable | Uso |
|---|---|
| `DATABASE_URL` | PostgreSQL de pagos y autenticación (`postgresql+psycopg://…`). |
| `DIFE_DATABASE_URL` | SQL Server del DIFE (`mssql+pyodbc://…?driver=ODBC+Driver+18+for+SQL+Server`). |
| `DICE_DATABASE_URL` | Oracle del DICE (`oracle+oracledb://…`). |
| `MONGODB_URL`, `MONGODB_DATABASE` | MongoDB de QA. No uses `cluster0` ni bases de otros proyectos. |
| `QA_TARGET_BASE_URL` | Destino del ejecutor de CP (en local, `http://127.0.0.1:8000`). |
| `AUTH_SECRET_KEY` | Clave privada de al menos 32 caracteres: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME` | Envío de correos por la API HTTPS de Brevo. |
| `PAYMENT_LIMIT_UVB`, `UVB_VALUE_CENTS` | Límite por operación (1.000 UVB) y valor de la UVB en centavos (supuesto; actualizar al valor vigente). |
| `QA_SECRET_PAYMENTS_API_TOKEN` | Ejemplo de secreto referenciable desde un CP como `{{secret:PAYMENTS_API_TOKEN}}`. |

Ningún secreto va al repositorio: solo `.env` (ignorado por git) o el gestor de secretos de la nube.

### Ejecución (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,postgres]"
Copy-Item .env.example .env          # luego completa los valores
python -m app.cli init-key-stores    # crea las tablas de DIFE y DICE (idempotente)
python -m app.cli create-initial-admin
python -m uvicorn app.main:app --reload
```

En otra terminal, la interfaz:

```powershell
cd web
npm install
npm run dev
```

La API queda en `http://127.0.0.1:8000` (salud en `/health`) y la interfaz en `http://127.0.0.1:5173`; Vite reenvía `/api` al backend. Las tablas de pagos y autenticación se crean al iniciar la API. `docker compose up --build` levanta la API con un PostgreSQL local de desarrollo cuando Docker está disponible.

## Acceso seguro y roles

El acceso requiere contraseña y un código MFA de seis dígitos enviado por correo. Las contraseñas se guardan con Argon2id; el código vence a los 5 minutos, admite 5 intentos y es de un solo uso. Hay límites de intentos por correo e IP. Las sesiones usan tokens opacos guardados como digest, con vencimiento por inactividad (30 minutos) y absoluto (8 horas). La cookie es `HttpOnly`, `SameSite=Strict` y `Secure` fuera de local, y toda operación que cambia estado exige token CSRF (`GET /auth/csrf` y cabecera `X-CSRF-Token`).

### Reenvío del código MFA

1. `POST /auth/login` valida la contraseña y envía el código. La respuesta es siempre la misma, para no revelar si la cuenta existe, y siempre emite la cookie `HttpOnly` `qalab_pending_login` (15 minutos).
2. Solo con contraseña válida se registra ese token (como digest) en `pending_logins`.
3. `POST /auth/resend-code` usa la cookie para emitir un código nuevo **sin volver a pedir la contraseña**; el código anterior queda invalidado.
4. Límites: 60 segundos entre envíos (`429` con `Retry-After`) y 3 códigos cada 15 minutos por usuario.
5. Al verificar el código (`POST /auth/verify-email-code`) se elimina el inicio pendiente. La interfaz muestra "Reenviar código" con cuenta regresiva.

### Roles

Los permisos se validan en el servidor; la interfaz solo oculta lo que el backend igual rechazaría.

| Acción | `admin` | `administrador` | `usuario` |
|---|---|---|---|
| Crear administradores | Sí | No | No |
| Crear usuarios | Sí | Sí | No |
| Crear épicas | Sí (único) | No | No |
| Asociar integrantes a épicas | Sí | Sí | No |
| Crear HU, CP e importar programas | No | Sí | No |
| Crear, asignar y reasignar tareas | Sí | Sí | No |
| Editar el JSON de un CP, ejecutarlo y avanzar tareas | Sí, si integra la épica | Sí, si integra la épica | Sí, si integra la épica |
| Registrar bugs y fixes | No | No | Sí |
| Avanzar el flujo de bugs (en fix, retest) | Sí | Sí | Sí, sin asignar responsables |
| Asignar responsables de bugs | Sí | Sí | No |
| Crear cuentas y registrar o administrar llaves | Sí | Sí | No (solo acciones personales sobre sus llaves) |

No hay usuarios ni contraseñas por defecto. El primer `admin` se crea con `python -m app.cli create-initial-admin` (solo funciona con la base vacía) y otros con `python -m app.cli create-admin`; el rol `admin` nunca se asigna por API. Cada cuenta creada desde la plataforma recibe un correo de bienvenida con su rol y quién la creó, nunca con la contraseña; si el correo falla, la cuenta igual queda creada y la respuesta lo indica con `notification_status: "failed"`.

## Llaves Bre-B (DIFE y DICE)

### Tipos de llave

Según la página pública de Banrep ("¿Cuáles tipos de Llave puedo tener?"), una persona puede registrar número de documento, celular, correo y una llave alfanumérica que inicia con `@`; los comercios usan además el código de comercio. `GET /keys/types` publica el catálogo y los formularios lo usan.

| Código | Tipo | Ejemplo | Forma canónica (supuesto del laboratorio) |
|---|---|---|---|
| `document` | Documento de identidad | `1023456789` | 5 a 15 dígitos; se quitan puntos y guiones |
| `phone` | Celular | `3001234567` | 10 dígitos que inician en 3; se acepta `+57` |
| `email` | Correo electrónico | `nombre@dominio.com` | En minúsculas |
| `alias` | Llave alfanumérica | `@ana2026` | `@` + 3 a 20 letras o números, en minúsculas |
| `merchant_code` | Código de comercio | `0012345` | 4 a 10 dígitos |

Banrep no publica longitudes ni formatos exactos: estas reglas son **supuestos**. La API valida y normaliza la llave en alta, ciclo de vida, resolución y pagos, así que `+57 300 123 4567` y `3001234567` son la misma llave.

### Registro con unicidad global

DIFE y DICE son componentes separados con bases independientes. El alta (`POST /difes/{spbvi_id}/keys`) sigue estos pasos:

1. DICE inserta una reserva `pending`, protegida por un índice único `(key_type, key_value)`.
2. DIFE guarda y activa la asociación local (`active`) con su propio índice único.
3. DICE pasa la reserva a `confirmed`.

Son transacciones locales separadas, no una transacción distribuida. Si un paso falla después de la reserva, la API responde `503` y reenviar la misma solicitud reanuda la activación. DICE garantiza la unicidad aun con registros simultáneos; una llave ya confirmada responde `409`. DICE conserva la referencia al producto de depósito para enrutar, pero no guarda saldos ni transacciones.

### Ciclo de vida

Los cuerpos JSON de cada operación están en `tests/fixtures/key_lifecycle/` y se ejecutan en las pruebas HTTP.

| Acción | Método y ruta | Permisos y comportamiento |
|---|---|---|
| Crear | `POST /difes/{spbvi_id}/keys` | `admin` o `administrador`; `owner_email` opcional. |
| Resolver en el DIFE | `GET /difes/{spbvi_id}/keys/resolve?key_type=…&key_value=…` | Cualquier rol; no devuelve el correo del titular. |
| Asignar titular | `PATCH /difes/{spbvi_id}/keys/owner` | `admin` o `administrador`; necesario para habilitar acciones personales en llaves sin titular. |
| Suspender | `POST /difes/{spbvi_id}/keys/suspend` con `suspension_type` `administrative` o `personal` | Administrativa: `admin` o `administrador`. Personal: solo el titular autenticado. |
| Reactivar | `POST /difes/{spbvi_id}/keys/reactivate` con `reactivation_type` `administrative` o `personal` | Administrativa: cualquier suspensión. Personal: solo el titular y solo si la suspensión fue personal. |
| Eliminar | `DELETE /difes/{spbvi_id}/keys` con motivo | `admin` o `administrador`; libera ambos índices y conserva la auditoría en `key_lifecycle_events` del DIFE. |

Suspender y reactivar ponen primero `pending` en DICE (la llave deja de resolverse en pagos inter-SPBVI), actualizan DIFE y confirman el estado final en DICE. Si algo falla, la API responde `503` y reenviar el mismo JSON completa la transición sin duplicar la auditoría.

## Cuentas y pagos

| Endpoint | Descripción |
|---|---|
| `POST /accounts` | Crea una cuenta simulada (`account_id`, `spbvi_id`, `balance_cents`). El saldo inicial se registra como asiento `opening`. `admin` o `administrador`; un identificador repetido responde `409`. |
| `POST /payments` | Pago intra-SPBVI: resuelve la llave **solo en el DIFE** del SPBVI de origen (el DICE recibe cero consultas) y mueve débito y crédito en una sola transacción. |
| `POST /payments/inter-spbvi` | Pago inter-SPBVI: resuelve la llave en el DICE, liquida con el MOL simulado y devuelve `pacs008_xml` y `pacs002_xml`. |
| `GET /payments/{operation_id}` | Estado de un pago y su equivalente pacs.002 (`ACCP`, `PDNG`, `RJCT`). Equivale en el laboratorio a una consulta pacs.028. |
| `GET /accounts/{account_id}/statement` | Extracto: saldo inicial, movimientos (con su `operation_id`), créditos, débitos, saldo final y si concilia con el saldo de la cuenta (`reconciled`). Equivale a camt.052/053. |

Reglas de los pagos:

- **Idempotencia:** repetir el mismo `operation_id` con los mismos datos devuelve el pago original (`200`, `replayed: true`) sin mover dinero; con datos distintos responde `409`. Un `operation_id` no se reutiliza entre flujos intra e inter.
- **Límite por operación:** el monto no puede superar `PAYMENT_LIMIT_UVB × UVB_VALUE_CENTS` (1.000 UVB). El pago intra responde `422`; el inter responde `422` con `pacs002_xml` `RJCT` y razón `AMOUNT_LIMIT_EXCEEDED`.
- **Rechazos sin efectos:** llave o cuenta inexistente (`404`), fondos insuficientes (`409`; en inter con pacs.002 `RJCT`), llave del mismo SPBVI por el flujo inter (`422`). Ningún rechazo ni falla parcial deja débito o crédito: todo se revierte.
- Las entradas del ledger de cada pago suman cero.

### Adaptador ISO 20022 de laboratorio

`app/domains/iso20022/messages.py` genera y valida `pacs.008.001.08` y `pacs.002.001.10` con XSD propios (`*-lab.xsd`) limitados a los campos implementados. **No son los XSD oficiales de ISO 20022 ni prueban conformidad con Banrep.** Versiones, campos y secuencia del flujo son supuestos hasta revisar el anexo 6 de la Circular DSP-465. `PDNG` solo se valida como mensaje, porque el MOL liquida de forma síncrona.

## Gestión QA

Jerarquía: **épica → HU → CP**, con **tareas** a nivel de épica y **ejecuciones**, **bugs** y **fixes** asociados a cada CP. La interfaz muestra primero esa planeación y después la ejecución.

### Casos de prueba: API REST con JSON

Todo CP ejecutable es una solicitud HTTP a la API REST con cuerpo JSON (por decisión del proyecto no se usan otros esquemas mientras no haga falta). Además de los criterios tipo Jira (descripción, precondiciones, pasos, resultado esperado), cada CP guarda su contrato:

```json
{
  "request_method": "POST",
  "request_path": "/payments",
  "request_query": {},
  "request_headers": {},
  "request_body": {"operation_id": "op-001", "amount_cents": 1250},
  "expected_status_codes": [201],
  "expected_response": {"status": "completed"}
}
```

`POST /qa/cases/{case_key}/execute` ejecuta el contrato contra `QA_TARGET_BASE_URL` y guarda método, URL, solicitud y respuesta redactadas, resultado y duración. El CP aprueba si el código HTTP está entre los esperados y, cuando hay `expected_response`, si la respuesta contiene esos campos con esos valores (comparación por subconjunto). Con `expected_response: null` solo se valida el código HTTP.

Los JSON no son fijos: cualquier integrante de la épica los actualiza desde **Calidad y pruebas → JSON** o con `PUT /qa/cases/{case_key}` (mismo contrato más `change_note`). Cada guardado vuelve a validar, incrementa `version`, guarda la versión anterior en `versions` (autor, fecha y motivo, visible en "Historial"), usa concurrencia optimista (`409` si otra edición se guardó antes) y avisa a la épica.

### Seguridad del ejecutor

Solo admite rutas relativas, métodos permitidos y el destino configurado; en local obliga a `localhost`, no sigue redirecciones y no acepta URLs absolutas. Los campos con nombres de credenciales se rechazan salvo referencias como `{{secret:PAYMENTS_API_TOKEN}}`, que se resuelven desde el entorno al ejecutar y nunca se guardan. La sesión y el CSRF de quien ejecuta se reenvían solo si el host destino coincide con el host de la solicitud entrante; por eso, en la nube, `QA_TARGET_BASE_URL` apunta al dominio propio de la Container App. Las respuestas registradas se limpian de secretos y se omiten si superan 1 MB.

### Bugs, fixes y tareas

- Bugs: `open → assigned → in_fix → ready_for_retest → closed`, o `reopened` si el retest falla. El responsable debe integrar la épica; el cierre exige retest aprobado.
- Tareas: `open → in_progress → done`; el usuario solo avanza las que tiene asignadas.

### Avisos por correo

Cada acción relevante guarda su aviso en el mismo documento Mongo y lo envía por Brevo a todos los integrantes de la épica, incluido quien la realizó. Si Brevo falla, la acción queda guardada y el aviso pendiente se reintenta con `POST /qa/notifications/retry` (`admin` o `administrador`). La entrega es de mejor esfuerzo, al menos una vez.

## Programa de pruebas ISO 20022 Bre-B

`qa_programs/iso20022-breb-rest-json.json` adapta a la plataforma el documento "Programa de Pruebas ISO 20022 – Ecosistema BREB BanRep (Enfoque API REST / JSON)": 19 HU, 30 CP ejecutables y 22 tareas (incluidas las subtareas). Se genera con `python qa_programs/build_iso20022_breb.py`; para cambiar datos o casos, edita el script y vuelve a generarlo.

Adaptaciones al laboratorio:

- **HU de preparación (`LAB-HU-000`):** crea cuentas fijas `qa-breb-*` en `spbvi-a` y `spbvi-b` y las llaves `@qabrebdestino` y `@qabrebremoto`. Aceptan `201` o `409`, así que el programa se puede ejecutar todas las veces que haga falta.
- **Mapeo de mensajes:** pain.001 → `POST /payments`; pacs.008/pacs.002 → `POST /payments/inter-spbvi`; pacs.028 → `GET /payments/{operation_id}`; camt.052/053 y conciliación → `GET /accounts/{id}/statement`.
- **HU sin endpoint** (pacs.004, camt.056, camt.029, camt.054, pain.002 independiente, resiliencia) se cargan sin CP y lo indican en su descripción; no se inventan casos que no se puedan ejecutar.
- Se corrigen erratas del documento: "BREG" → Bre-B, "Camato" → CAMT y cuentas CLABE (formato mexicano) → producto de depósito local.

Cómo cargarlo y ejecutarlo:

1. El `admin` crea la épica (título y descripción en el bloque `epic` del archivo) y asocia al equipo.
2. Un `administrador` integrante abre la épica en **Calidad y pruebas → Importar programa** y sube el archivo (o usa `POST /qa/epics/{epic_key}/import`). Todo se valida antes de guardar; lo que ya existe con la misma `ref` se omite y el equipo recibe un solo correo resumen.
3. Un `administrador` ejecuta los CP de `LAB-HU-000` (crear cuentas y llaves es administrativo).
4. Cualquier integrante ejecuta cada CP desde su detalle, uno a uno, según el que necesite probar; los que dependen de datos previos indican en sus precondiciones que primero se ejecute la preparación. `tests/test_qa_program.py` importa el programa y comprueba que los 30 CP aprueban en dos rondas seguidas.

Como alternativa a los pasos 1 y 2, un operador con acceso a las bases puede cargarlo por CLI:

```bash
python -m app.cli seed-program qa_programs/iso20022-breb-rest-json.json CORREO_ADMIN CORREO_ADMINISTRADOR
```

Crea la épica con todos los usuarios activos como integrantes (o reutiliza la que tenga el mismo título) e importa el programa con las mismas reglas y avisos que la interfaz. Se puede reejecutar sin duplicar nada. En el laboratorio se corre con las variables de `.env.lab` cargadas solo en esa terminal; así quedó cargada la épica `EPIC-00001`.

## Correos transaccionales

Todos los correos (código MFA, bienvenida y avisos QA) usan una sola plantilla (`app/core/email_templates.py`): HTML con CSS inline, una columna adaptable a móvil y escritorio, paleta de la interfaz, sin JavaScript, imágenes, fuentes ni hojas externas, y con alternativa en texto plano. Todo valor dinámico se escapa. No incluyen credenciales, tokens ni datos sensibles de pagos; la única excepción es el código MFA, que se muestra con su vencimiento y el aviso de no compartirlo.

## Interfaz web

React + TypeScript + Vite en `web/`, con tipografía del sistema y sin recursos externos. Incluye:

- Acceso con MFA y reenvío del código.
- Panel inicial con las épicas y su avance (HU, CP y tareas abiertas), "Mis tareas", estado del entorno y banner.
- **Calidad y pruebas:** jerarquía épica → HU → CP, tareas, editor e historial del JSON de cada CP, ejecución, importación de programas y creación según el rol.
- Llaves (alta, consulta y ciclo de vida con el catálogo de tipos), pagos intra/inter y cuentas.
- Usuarios y roles; tema claro y oscuro; diseño adaptable desde 320 px.

La paleta toma como referencia el carácter institucional de Banrep, sin afirmar que reproduce sus colores oficiales. Todo el texto del producto usa español neutro.

## Pruebas automatizadas

```powershell
python -m pytest                       # backend: unitarias, HTTP e integración del programa QA
cd web; npm run build; npm run test:e2e  # compilación y recorridos E2E con Playwright
```

- **Backend:** usan SQLite en memoria, `mongomock` y un emisor de correo falso; no tocan las bases configuradas ni envían correos. Cubren las pruebas de aceptación de la fase 1 (llaves duplicadas en paralelo, rechazos sin efectos, idempotencia, rollback, ledger que suma cero, cero consultas al DICE en pagos intra), el ciclo de llaves, pagos inter con ISO 20022, autenticación, MFA y reenvío, roles, gestión QA, correos y el programa ISO 20022 completo.
- **E2E (Playwright):** inician una API aislada en el puerto 8010 y Vite en el 5174, con credenciales y datos temporales. Recorren MFA, ejecución y edición versionada de un CP, ciclo de llaves y pagos intra/inter, en anchos de 320 a 375 px y sin desbordamiento horizontal. `QALAB_E2E_PYTHON` permite elegir el intérprete.

## CI/CD y laboratorio en la nube

`.github/workflows/ci.yml` ejecuta en cada push y pull request las pruebas del backend, la compilación de React y los E2E. En un push a `main` que pasa la CI, el job **Desplegar laboratorio** (activo con la variable `LAB_DEPLOY_ENABLED=true`) publica la API (imagen etiquetada con el SHA del commit) y el frontend. GitHub entra a Azure por OIDC con la identidad `qalabspbvi-github-lab`, limitada a `AcrPush` sobre el registro, `Contributor` sobre la Container App y `Reader` sobre el grupo; el único secreto en GitHub es el token de Static Web Apps del environment `lab`.

El laboratorio reparte sus componentes entre proveedores, cada uno con bases nuevas y dedicadas:

| Componente | Proveedor | Recurso |
|---|---|---|
| API FastAPI | Azure Container Apps (eastus2) | Imagen en Azure Container Registry |
| Frontend React | Azure Static Web Apps | Publicado detrás de Azure Front Door |
| Entrada pública | Azure Front Door | `/` → React; `/api/*` → API sin el prefijo `/api` |
| Pagos y autenticación | Neon (PostgreSQL, us-east-2) | Proyecto dedicado |
| DIFE | Azure SQL Database (centralus) | Base dedicada (eastus2 no admitía servidores SQL nuevos) |
| DICE | OCI Autonomous Database (sa-bogota-1) | Base Always Free nueva, compartimento `qalabspbvi-lab`, usuario `QALABDICE`, TLS |
| QA | MongoDB Atlas (AWS us-east-1) | Proyecto `QALabSPBVI-lab`, cluster M0, usuario limitado a `qalabspbvi_qa_lab` |
| Secretos | Azure Key Vault | La API los lee con identidad administrada |

La infraestructura de Azure está en `infra/azure/main.bicep` y se aprovisiona por CLI tras revisar el `what-if`. La aplicación es pública por Front Door; las bases no: OCI y Atlas solo aceptan la IP de salida de la Container App y la de administración, y Azure SQL solo servicios de Azure y la IP de administración. Si Azure cambia la IP de salida, hay que actualizar esas listas. Las cadenas de conexión del laboratorio viven solo en Key Vault y en un `.env.lab` local ignorado por git. El primer administrador del laboratorio se crea con `python -m app.cli create-initial-admin`, ejecutado con las variables de `.env.lab` cargadas solo en esa terminal.

## Estructura del repositorio

```text
app/
  api/         Rutas HTTP y esquemas
  core/        Configuración, seguridad, correo y plantilla de correos
  db/          Modelos SQLAlchemy y sesiones
  domains/
    auth/      Credenciales, MFA, reenvío y sesiones
    keys/      Catálogo de tipos, DIFE/DICE y ciclo de vida
    payments/  Pagos intra/inter-SPBVI, límite por operación, MOL y ledger
    iso20022/  Adaptador XML de laboratorio y XSD propios
    qa/        Gestión tipo Jira, ejecutor HTTP, importación y avisos
  cli.py       Comandos de bootstrap (admin inicial, tablas de llaves)
  main.py      Punto de entrada FastAPI
qa_programs/   Programas de prueba importables y su generador
tests/         Pruebas del backend y servidor aislado para E2E
web/           Interfaz React/TypeScript y pruebas Playwright (web/e2e)
infra/azure/   Bicep del laboratorio
```

## Supuestos y pendientes

Supuestos (se ajustan al recibir la especificación oficial):

- Tipos y orden de mensajes ISO 20022, perfiles `pacs.008.001.08` / `pacs.002.001.10` y su mapeo a JSON (anexo 6 de la Circular DSP-465).
- Formatos y longitudes de las llaves, valor de la UVB y el mapeo de pain.001, pacs.028 y camt.052/053 a los endpoints del laboratorio.

Pendientes:

- Endpoints para pacs.004 (devoluciones), camt.056 (cancelaciones), camt.029 (investigaciones), camt.054 (notificaciones) y pain.002 independiente; filtros por fecha en el extracto.
- Mock server para simular latencia, timeouts y errores de red (HU-018, TASK-004, TASK-010, TASK-013).
- Validación con JSON Schema por tipo de mensaje, monitoreo de latencia y pruebas de seguridad OWASP.
- Cola de eventos en AWS SQS, logs en Neon y entorno de producción con aprobación manual.
- Pantalla para registrar y gestionar bugs y fixes (el backend ya los soporta).
