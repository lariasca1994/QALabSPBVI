# QALabSPBVI

<p>
  <a href="https://qalabspbvi.vercel.app"><img src="docs/demo-badge.svg" alt="Abrir la demo en vivo" height="32"></a>
  <a href="https://frontend-nine-topaz-99.vercel.app"><img src="https://portafolio-status.onrender.com/api/status/qalabspbvi/badge.svg" alt="Estado en vivo del proyecto" height="32"></a>
  <a href="https://d4i3vsgw7xwmh.cloudfront.net"><img src="https://portafolio-status.onrender.com/api/status/qalabspbvi/qa-badge.svg" alt="Fecha y resultado de la última prueba E2E" height="32"></a>
</p>

![Python](https://img.shields.io/badge/Python_3.13-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React_TypeScript-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)
![Playwright](https://img.shields.io/badge/Playwright-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/Neon_PostgreSQL-00E599?style=for-the-badge&logo=postgresql&logoColor=black)
![Azure SQL](https://img.shields.io/badge/Azure_SQL-0078D4?style=for-the-badge&logo=microsoftazure&logoColor=white)
![Oracle](https://img.shields.io/badge/Oracle_ADB-F80000?style=for-the-badge&logo=oracle&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB_Atlas-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![TiDB](https://img.shields.io/badge/TiDB_Cloud_(MySQL)-4479A1?style=for-the-badge&logo=mysql&logoColor=white)
![Azure Container Apps](https://img.shields.io/badge/Azure_Container_Apps-0078D4?style=for-the-badge&logo=microsoftazure&logoColor=white)
![AWS Lambda](https://img.shields.io/badge/AWS_SQS_+_Lambda-FF9900?style=for-the-badge&logo=awslambda&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-000000?style=for-the-badge&logo=vercel&logoColor=white)
![Render](https://img.shields.io/badge/Render-46E3B7?style=for-the-badge&logo=render&logoColor=black)

Laboratorio simulado de pagos inmediatos interoperables, inspirado en los esquemas nacionales de pagos inmediatos con llaves. Sirve para practicar y mostrar aseguramiento de calidad automatizado: pruebas de API REST con JSON, mensajería ISO 20022 de laboratorio y recorridos E2E.

Es un proyecto de portafolio: **no se conecta a ninguna infraestructura real de pagos** y la liquidación (MOL) es una simulación.

### En pocas palabras

- **Qué hace:** simula un ecosistema de pagos inmediatos con llaves. Registra llaves (celular, correo, documento, alfanumérica `@` y código de comercio) en el directorio de cada SPBVI (DIFE) y en el central (DICE). Procesa pagos entre cuentas del mismo SPBVI o de SPBVI distintos, con idempotencia, límite de 1.000 UVB y mensajes pacs.008 / pacs.002 de laboratorio.
- **Para el equipo QA:** una plataforma tipo Jira (épica → HU → CP, tareas, bugs y fixes). Cada CP es una solicitud REST con JSON que se ejecuta con un clic. El resultado muestra método, URL, solicitud, respuesta y veredicto. El sistema genera las llaves de prueba según su tipo, y las transacciones las toman de la lista de llaves de la épica.
- **Cómo probarlo:** entra a la [demo](https://qalabspbvi.vercel.app) con una cuenta creada por un administrador (acceso con contraseña y código por correo). La épica `EPIC-00001` ya trae el programa ISO 20022 cargado. Para correrlo en tu equipo, ve a [Instalación](#instalación) y [Puesta en marcha](#puesta-en-marcha).

## Demo en vivo

**Aplicación:** [qalabspbvi.vercel.app](https://qalabspbvi.vercel.app)

**Documentación de la API:** `/api/docs` en la demo o `http://127.0.0.1:8000/docs` en local.

No hay cuentas públicas ni contraseñas por defecto: el acceso lo da un administrador.

## Qué reúne este proyecto

QALabSPBVI es el proyecto destacado del portafolio: integra en un solo sistema lo que los demás proyectos trabajan por separado.

| Área | Qué demuestra aquí |
|---|---|
| **Dominio de pagos** | Modelado de un ecosistema de pagos inmediatos con llaves: directorio federado de llaves por entidad (DIFE) y central (DICE) con unicidad global; pagos intra-SPBVI (llave en DIFE) e inter-SPBVI (llave en DICE, liquidación en un MOL simulado); idempotencia, límite de 1.000 UVB y ledger que siempre suma cero. Pagos con código QR (formato EMVCo) estáticos y dinámicos de un solo uso. |
| **Mensajería ISO 20022** | pacs.008 y pacs.002 (generados por un gateway aparte con respaldo local), pacs.004 (devoluciones), camt.056 y camt.029 (cancelación e investigación), camt.054 (notificaciones) y pain.002 (estado para el cliente), todos validados contra XSD propios de laboratorio. |
| **Aseguramiento de calidad** | Plataforma tipo Jira con épicas, HU, CP, tareas, bugs y fixes. Cada CP es una solicitud REST/JSON que se ejecuta con un clic y muestra método, URL, solicitud y respuesta. Cada respuesta se valida además contra el contrato OpenAPI (JSON Schema) y el ejecutor reintenta los fallos de red. Incluye un programa ISO 20022 de 60 CP que pasa completo en la nube, con llaves generadas según su tipo, y un mock server de fallos de red para las pruebas de resiliencia. |
| **Backend y datos** | Monolito modular en FastAPI con cinco motores de base distintos, uno por dominio: PostgreSQL, SQL Server, Oracle, MongoDB y MySQL (TiDB). Ninguno se comparte, y la coordinación entre DIFE y DICE, y entre cobros QR y pagos, es recuperable, sin transacciones distribuidas. |
| **Seguridad** | Contraseñas Argon2id, MFA por correo con reenvío, roles validados en el servidor, sesiones con vencimiento, CSRF y secretos solo en gestores de secretos. |
| **Cloud y DevOps** | Ocho plataformas cloud con una función cada una (ver [Plataformas](#plataformas)). CI/CD con GitHub Actions por OIDC, sin claves guardadas, y bases que se pausan solas sin uso. |
| **Pruebas** | 190 pruebas automáticas de backend (aceptación, integración y programa completo) y recorridos E2E con Playwright en anchos móviles, incluida la lectura de QR con cámara simulada y la app instalable. |
| **Trazabilidad** | Registro de cada solicitud en una base propia, con identificador de correlación y `operation_id` para seguir todos los mensajes de un pago, pantalla de auditoría y exportación CSV. |

## Funcionalidades

**Llaves de pago (DIFE y DICE)**
- Catálogo de tipos de llave con validación y forma canónica (`GET /keys/types`).
- Registro con unicidad global: DICE reserva (`pending`), DIFE activa y DICE confirma. Nunca hay duplicados, ni con registros simultáneos.
- Ciclo de vida completo: asignación de titular, suspensión y reactivación (administrativa o personal) y eliminación con auditoría.
- Coordinación DIFE/DICE recuperable: si un paso falla, la misma solicitud se reintenta sin duplicar la auditoría.

**Cuentas y pagos**
- Pago intra-SPBVI: resuelve la llave solo en el DIFE (el DICE recibe cero consultas) y liquida en una transacción.
- Pago inter-SPBVI: resuelve en el DICE, liquida con el MOL simulado y devuelve `pacs008_xml` y `pacs002_xml`.
- Idempotencia por `operation_id`, límite de 1.000 UVB por operación y rechazos sin efectos sobre los saldos.
- Consulta de estado (equivalente a pacs.028) y extracto conciliado (equivalente a camt.052/053).
- Devoluciones totales o parciales (pacs.004) que nunca superan el monto pagado.
- Solicitud de cancelación (camt.056) que abre una investigación; el SPBVI receptor la acepta, y entonces devuelve el pago con motivo FOCR, o la rechaza con motivo (camt.029).
- Notificaciones de crédito y débito por cuenta (camt.054) y reporte de estado para el cliente (pain.002), también en las respuestas del pago intra.

**Pagos con QR**
- QR en formato EMVCo, como el estándar de las entidades administradoras de pagos colombianas: llave Bre-B (campo 26), monto (54), identificador de la transacción (90), hash de seguridad (91) y CRC (63).
- QR estático (quien paga elige el monto), estático con monto fijo y cobro dinámico de un solo uso con vencimiento y anulación.
- El pago es intra-SPBVI si la llave está en el DIFE de la cuenta origen (sin consultar el DICE) o inter-SPBVI por DICE y MOL, con la misma idempotencia, límite de UVB y mensajes ISO 20022 de los demás pagos.
- Un QR alterado se rechaza por CRC o por firma, sin mover dinero.
- Pantalla "Código QR" que genera el QR y lo lee con la cámara, desde una imagen o pegando su contenido.

**App instalable (PWA)**
- Se instala desde el navegador en Android, iOS, Windows, macOS y Linux, con ícono propio y acceso directo a los pagos con QR.
- Abre sin conexión con una página propia; nunca guarda respuestas de la API.
- No trabaja en segundo plano: sin sincronización, notificaciones push ni consultas periódicas con la app oculta.

**Plataforma QA**
- Épicas, HU, CP, tareas, bugs y fixes con permisos por rol validados en el servidor.
- CP ejecutables (API REST + JSON) con editor del JSON y versionado: cada cambio guarda la versión anterior en el historial.
- Llaves generadas automáticamente según su tipo y lista de llaves por épica, que las transacciones eligen según el tipo que indica el CP.
- Importación de programas completos (HU, CP y tareas) y plantillas para crear la documentación.
- Programa ISO 20022 de laboratorio: 19 HU, 60 CP ejecutables y 22 tareas.
- Validación de contrato: cada respuesta se compara con el JSON Schema del endpoint en OpenAPI; si no lo cumple, el CP falla y muestra los errores.
- Reintentos automáticos ante cortes de conexión y 502/503/504, con cada intento registrado en la ejecución.
- Pantalla de bugs y fixes: reporte desde una ejecución fallida, asignación, registro de fixes, retest e historial.
- Avisos por correo a la épica con plantilla HTML propia y texto plano.

**Resiliencia y trazabilidad**
- Mock server (`/mock/*`) que simula latencia, timeout de un servicio aguas arriba, errores HTTP, cortes de conexión y un servicio inestable.
- Registro asíncrono de cada solicitud (método, ruta, código, duración, usuario y `operation_id`) con encabezado `X-Request-ID`, pantalla de auditoría y exportación CSV.

**Acceso**
- Contraseña (Argon2id) y código MFA por correo, con reenvío sin volver a pedir la contraseña.
- Credenciales inválidas informadas con un único mensaje, exista o no la cuenta.
- Cambio de la propia contraseña (cierra las demás sesiones) y recuperación con un código de un solo uso por correo.
- Activación y desactivación de cuentas desde Usuarios y roles; desactivar cierra las sesiones de la cuenta.
- Registro público de demostración: crea siempre una cuenta `usuario`, sin épica, que queda pendiente hasta que un administrador la asocie a una. Tiene límite de registros por conexión y no envía correos.
- Roles `admin`, `administrador` y `usuario`; sesiones con vencimiento por inactividad y absoluto, y protección CSRF.

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2 (monolito modular por dominios) |
| Frontend | React, TypeScript, Vite; tema claro y oscuro; instalable como PWA (manifiesto y service worker) |
| Pagos y autenticación | PostgreSQL (Neon) |
| DIFE | SQL Server (Azure SQL Database) con pyodbc y ODBC Driver 18 |
| DICE | Oracle Autonomous Database (OCI) con python-oracledb |
| QA | MongoDB (Atlas) con pymongo |
| Logs | PostgreSQL (Neon), base propia |
| Cobros QR | MySQL (TiDB Cloud) con PyMySQL y TLS; QR con `qrcode` y lectura con `jsQR` |
| Contratos | jsonschema sobre el OpenAPI de la API |
| ISO 20022 | lxml con XSD propios de laboratorio; gateway propio en Render |
| Avisos | AWS SQS (con DLQ) y AWS Lambda que entrega por Brevo |
| Pruebas | pytest con mongomock y SQLite en memoria; Playwright para E2E |
| Infraestructura | Bicep (Azure) y CloudFormation (AWS) |
| CI/CD | GitHub Actions con OIDC hacia Azure y AWS; imágenes en GitHub Container Registry; Vercel con integración de Git |

## Conexiones externas

| Servicio | Uso | Obligatorio |
|---|---|---|
| PostgreSQL | Pagos, ledger, usuarios y sesiones | Sí (en local se puede usar SQLite temporal) |
| SQL Server | DIFE: llaves por SPBVI y auditoría | Sí |
| Oracle | DICE: índice central de llaves | Sí |
| MongoDB | Artefactos QA: épicas, HU, CP, ejecuciones y llaves | Sí |
| Brevo (API HTTP) | Código MFA, bienvenida y avisos QA | Sí para iniciar sesión (el MFA llega por correo) |
| AWS SQS + Lambda | Cola y entrega de los avisos QA | No: sin `NOTIFICATIONS_QUEUE_URL` los avisos salen directo por Brevo |
| Gateway ISO 20022 (Render) | Genera los pacs.008 / pacs.002 | No: sin `ISO_GATEWAY_URL` o si no responde, se generan en proceso |
| PostgreSQL de logs | Registro de solicitudes y auditoría | No: sin `LOGS_DATABASE_URL` no se registra nada |
| MySQL (TiDB Cloud) | Cobros con QR dinámicos | Sí en la nube; en local, sin `QR_DATABASE_URL`, se usa SQLite temporal |

Cada componente usa una base **nueva, vacía y dedicada**. Ninguna se comparte ni reutiliza datos de otros proyectos.

## Arquitectura

<p align="center">
  <img src="docs/arquitectura.svg" alt="Diagrama de arquitectura: Vercel publica el frontend React y reenvía /api a la API FastAPI en Azure Container Apps; la API usa PostgreSQL en Neon, SQL Server en Azure SQL (DIFE), Oracle ADB en OCI (DICE), MongoDB Atlas (QA), AWS SQS y Lambda para avisos, un gateway ISO 20022 en Render y Brevo para correos; GitHub Actions despliega por OIDC" width="100%">
</p>

- **Vercel** publica el frontend React y reenvía `/api/*` a la API en **Azure Container Apps** (sin el prefijo). Para el navegador todo es el mismo dominio, así que las cookies siguen siendo `SameSite=Strict`.
- La **API FastAPI** está organizada por dominios: autenticación, llaves (DIFE/DICE), pagos con MOL simulado, adaptador ISO 20022 y gestión QA con su ejecutor de CP.
- Cada dominio persiste en su propia nube: pagos y autenticación en **Neon**, DIFE en **Azure SQL**, DICE en **Oracle ADB (OCI)**, QA en **MongoDB Atlas** y cobros QR en **TiDB Cloud** (MySQL).
- Los avisos QA van a una cola **AWS SQS** y una **Lambda** los entrega por Brevo. Los mensajes que fallan cinco veces pasan a una cola de fallidos (DLQ). La API solo puede enviar a la cola.
- El código MFA no usa la cola: sale directo por Brevo, para no sumarle demora al inicio de sesión.
- Los mensajes pacs.008 / pacs.002 los genera un **gateway ISO 20022 en Render**, un servicio sin estado. La API vuelve a validar el XML que recibe. Si el gateway no responde, lo genera en proceso, así un pago ya liquidado siempre tiene su mensaje. La respuesta indica quién lo generó en `iso_gateway`.
- El núcleo del dominio no depende del formato de mensaje: el adaptador ISO 20022 recibe datos neutrales del pago. Todos los montos son enteros en centavos.
- Los secretos viven en **Key Vault** y la API los lee con identidad administrada. Las bases exigen TLS y credenciales propias de cada componente.

## Plataformas

Cada plataforma cumple una función distinta; ninguna concentra todo el sistema.

| Plataforma | Qué hace en QALabSPBVI |
|---|---|
| **Vercel** | Publica la interfaz React y reenvía `/api/*` a la API, de modo que el navegador ve un solo dominio y las cookies siguen siendo `SameSite=Strict`. Se publica sola en cada push. |
| **Azure Container Apps** | Ejecuta la API FastAPI: autenticación con MFA, llaves DIFE/DICE, pagos intra e inter-SPBVI, MOL simulado, gestión QA y el ejecutor de CP. Escala a cero sin uso. |
| **Azure SQL Database** | DIFE: directorio federado de llaves de cada SPBVI y auditoría de su ciclo de vida. Resuelve las llaves de los pagos **intra-SPBVI**. |
| **Azure Key Vault** | Guarda las cadenas de conexión y claves de la API, que las lee con identidad administrada. |
| **Oracle Cloud (OCI)** | DICE en Autonomous Database: índice central de llaves con unicidad global. Resuelve las llaves de los pagos **inter-SPBVI**. |
| **Neon** | PostgreSQL de cuentas, ledger, pagos, usuarios y sesiones; ahí liquidan los pagos intra e inter. En una base aparte guarda el registro de solicitudes que alimenta la auditoría. |
| **MongoDB Atlas** | Artefactos QA: épicas, HU, CP con su JSON versionado, ejecuciones, bugs, fixes y la lista de llaves de cada épica. |
| **TiDB Cloud** | MySQL de los cobros con QR: cada cobro dinámico con su monto, vencimiento y estado. Escala a cero sin uso. |
| **AWS** | Cola SQS con DLQ y Lambda que entrega por Brevo los avisos QA. La API solo puede enviar a la cola; la API key de Brevo vive en SSM Parameter Store. |
| **Render** | Gateway ISO 20022: servicio aparte que genera los pacs.008 / pacs.002 de laboratorio. Se suspende sin uso; mientras despierta, la API los genera en proceso. |
| **Brevo** | Envía el código MFA, la bienvenida y los avisos QA. |
| **GitHub** | Código, CI/CD con Actions (OIDC hacia Azure y AWS, sin claves guardadas) e imágenes públicas en Container Registry. |

## Estructura

```
app/
├── api/              Rutas HTTP y esquemas
├── core/             Configuración, seguridad, correo y plantilla de correos
├── db/               Modelos SQLAlchemy y sesiones
├── domains/
│   ├── auth/         Credenciales, MFA, reenvío y sesiones
│   ├── keys/         Catálogo de tipos, DIFE/DICE y ciclo de vida
│   ├── payments/     Pagos intra/inter-SPBVI, límite por operación, MOL y ledger
│   ├── iso20022/     Adaptador XML de laboratorio y XSD propios
│   └── qa/           Gestión tipo Jira, ejecutor HTTP, marcadores, importación y avisos
├── cli.py            Comandos de operación (admin inicial, tablas de llaves, seed-program)
└── main.py           Punto de entrada FastAPI
services/
├── notifier/         Lambda de AWS que entrega los avisos QA por Brevo
└── iso20022_gateway/ Gateway ISO 20022 para Render (FastAPI + Dockerfile)
qa_programs/
├── build_iso20022_breb.py        Generador del programa ISO 20022
├── iso20022-breb-rest-json.json  Programa importable
└── plantillas/                   Plantillas de épica, programa, HU, CP, tarea, bug y fix
tests/                Pruebas del backend y servidor aislado para E2E
web/                  Interfaz React/TypeScript y pruebas Playwright (web/e2e)
infra/azure/          Bicep del laboratorio
infra/aws/            CloudFormation de la cola, la DLQ, la Lambda y sus permisos
docs/                 Diagrama e insignias del README
```

## Requisitos

- Python 3.11 o superior y Node.js 20 o superior.
- Bases nuevas y dedicadas: PostgreSQL, SQL Server con ODBC Driver 18, Oracle (por ejemplo Oracle Free con `FREEPDB1`) y MongoDB.
- Una cuenta de Brevo con remitente verificado, para enviar el código MFA y los avisos.

## Instalación

```powershell
git clone https://github.com/lariasca1994/QALabSPBVI.git
cd QALabSPBVI
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,postgres]"
Copy-Item .env.example .env
cd web; npm install; cd ..
```

Completa `.env`. Los nombres deben coincidir exactamente: la configuración ignora las variables desconocidas, así que un nombre distinto deja la conexión en su valor por defecto sin avisar.

| Variable | Uso |
|---|---|
| `DATABASE_URL` | PostgreSQL de pagos y autenticación (`postgresql+psycopg://…`) |
| `DIFE_DATABASE_URL` | SQL Server del DIFE (`mssql+pyodbc://…?driver=ODBC+Driver+18+for+SQL+Server`) |
| `DICE_DATABASE_URL` | Oracle del DICE (`oracle+oracledb://…`) |
| `MONGODB_URL`, `MONGODB_DATABASE` | MongoDB de QA |
| `QA_TARGET_BASE_URL` | Destino del ejecutor de CP (en local, `http://127.0.0.1:8000`) |
| `AUTH_SECRET_KEY` | Clave de al menos 32 caracteres: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, `BREVO_SENDER_NAME` | Envío de correos por la API de Brevo |
| `PAYMENT_LIMIT_UVB`, `UVB_VALUE_CENTS` | Límite por operación (1.000 UVB) y valor de la UVB en centavos (supuesto; actualizar al vigente) |
| `QA_SECRET_…` | Secretos que un CP referencia como `{{secret:…}}`, sin guardarlos |
| `NOTIFICATIONS_QUEUE_URL`, `AWS_REGION` | Cola SQS de avisos (opcional); credenciales en `AWS_ACCESS_KEY_ID` y `AWS_SECRET_ACCESS_KEY` |
| `ISO_GATEWAY_URL`, `ISO_GATEWAY_TOKEN` | Gateway ISO 20022 en Render (opcional) |
| `LOGS_DATABASE_URL` | PostgreSQL de logs para la auditoría (opcional) |

Ningún secreto va al repositorio: solo `.env` (ignorado por git) o el gestor de secretos de la nube.

## Puesta en marcha

```powershell
python -m app.cli init-key-stores        # crea las tablas de DIFE y DICE (idempotente)
python -m app.cli create-initial-admin   # primer admin; solo funciona con la base vacía
python -m uvicorn app.main:app --reload
```

En otra terminal:

```powershell
cd web
npm run dev
```

La API queda en `http://127.0.0.1:8000` (salud en `/health`) y la interfaz en `http://127.0.0.1:5173`. Vite reenvía `/api` al backend. Las tablas de pagos y autenticación se crean al iniciar la API.

`docker compose up --build` levanta la API con un PostgreSQL local cuando Docker está disponible.

Para cargar un programa de pruebas en una épica nueva (o actualizar la existente):

```powershell
python -m app.cli seed-program qa_programs/iso20022-breb-rest-json.json CORREO_ADMIN CORREO_ADMINISTRADOR --actualizar
```

### Usuarios y roles

Los permisos se validan en el servidor; la interfaz solo oculta lo que el backend igual rechazaría.

| Acción | `admin` | `administrador` | `usuario` |
|---|---|---|---|
| Crear administradores | Sí | No | No |
| Crear usuarios | Sí | Sí | No |
| Desactivar o reactivar cuentas | Administradores y usuarios | Solo usuarios | No |
| Cambiar o recuperar la propia contraseña | Sí | Sí | Sí |
| Crear épicas | Sí (único) | No | No |
| Asociar integrantes a épicas | Sí | Sí | No |
| Crear HU, CP e importar programas | No | Sí | No |
| Crear, asignar y reasignar tareas | Sí | Sí | No |
| Editar el JSON de un CP, ejecutarlo y avanzar tareas | Si integra la épica | Si integra la épica | Si integra la épica |
| Registrar bugs y fixes | No | No | Sí |
| Asignar responsables de bugs | Sí | Sí | No |
| Crear cuentas y registrar o administrar llaves | Sí | Sí | Solo acciones personales sobre sus llaves |

El primer `admin` se crea por CLI y los siguientes con `python -m app.cli create-admin`. El rol `admin` nunca se asigna por API. Cada cuenta nueva recibe un correo de bienvenida con su rol, nunca con la contraseña.

### Acceso y reenvío del código

1. `POST /auth/login` valida la contraseña y envía un código de seis dígitos. La respuesta es la misma exista o no la cuenta, y emite la cookie `HttpOnly` `qalab_pending_login` (15 minutos).
2. `POST /auth/resend-code` usa esa cookie para emitir un código nuevo sin volver a pedir la contraseña; el anterior queda invalidado. Hay 60 segundos entre envíos y un máximo de 3 códigos cada 15 minutos.
3. `POST /auth/verify-email-code` abre la sesión: vence a los 30 minutos sin actividad y a las 8 horas en total.

El código vence a los 5 minutos, admite 5 intentos y es de un solo uso. Toda operación que cambia estado exige el token CSRF (`GET /auth/csrf` y cabecera `X-CSRF-Token`).

### Llaves de pago

Los tipos y formatos son **supuestos** del laboratorio:

| Código | Tipo | Ejemplo | Forma canónica |
|---|---|---|---|
| `document` | Documento de identidad | `1023456789` | 5 a 15 dígitos |
| `phone` | Celular | `3001234567` | 10 dígitos que inician en 3; se acepta `+57` |
| `email` | Correo electrónico | `nombre@dominio.com` | En minúsculas |
| `alias` | Llave alfanumérica | `@ana2026` | `@` + 3 a 20 letras o números |
| `merchant_code` | Código de comercio | `0012345` | 4 a 10 dígitos |

| Acción | Método y ruta |
|---|---|
| Crear | `POST /difes/{spbvi_id}/keys` |
| Listar confirmadas | `GET /difes/{spbvi_id}/keys?key_type=…` (sin datos del titular) |
| Resolver | `GET /difes/{spbvi_id}/keys/resolve?key_type=…&key_value=…` |
| Asignar titular | `PATCH /difes/{spbvi_id}/keys/owner` |
| Suspender / reactivar | `POST /difes/{spbvi_id}/keys/suspend` · `POST /difes/{spbvi_id}/keys/reactivate` |
| Eliminar | `DELETE /difes/{spbvi_id}/keys` (libera los índices y conserva la auditoría) |

### Cuentas y pagos

Un SPBVI existe desde que una cuenta o una llave usa su código. En la interfaz, la cuenta origen, el SPBVI destino y la llave destino se eligen de listas: en intra el destino es el mismo SPBVI de la cuenta origen y en inter se elige entre los demás. La llave también se puede escribir a mano para probar rechazos. Al crear una cuenta o registrar una llave se elige un SPBVI existente o se crea uno con "Nuevo SPBVI…".

| Método y ruta | Descripción |
|---|---|
| `GET /spbvis` | SPBVI existentes con su número de cuentas y llaves confirmadas |
| `GET /accounts` | Cuentas con su SPBVI y saldo (filtro opcional `spbvi_id`) |
| `POST /accounts` | Crea una cuenta simulada con saldo inicial |
| `POST /payments` | Pago intra-SPBVI |
| `POST /payments/inter-spbvi` | Pago inter-SPBVI con pacs.008 / pacs.002 de laboratorio |
| `GET /payments/{operation_id}` | Estado del pago (`ACCP`, `PDNG`, `RJCT`) |
| `GET /accounts/{account_id}/statement` | Extracto con movimientos, totales y conciliación |
| `POST /payments/{operation_id}/returns` | Devolución total (sin `amount_cents`) o parcial con pacs.004 |
| `GET /payments/{operation_id}/returns` | Devoluciones del pago |
| `POST /payments/{operation_id}/cancellation-requests` | Solicitud de cancelación (camt.056); responde `202` con la investigación pendiente |
| `GET /investigations/{cancellation_id}` | Estado de la investigación con camt.056 y camt.029 |
| `POST /investigations/{cancellation_id}/resolution` | El SPBVI receptor acepta (devolución FOCR) o rechaza con motivo (camt.029) |
| `GET /accounts/{account_id}/notifications` | Créditos y débitos de la cuenta con camt.054 (filtro opcional por `operation_id`) |
| `GET /payments/{operation_id}/status-report` | Reporte de estado para el cliente (pain.002) |

Reglas de los pagos:

- **Idempotencia:** el mismo `operation_id` con los mismos datos devuelve el pago original (`200`, `replayed: true`). Con datos distintos responde `409`.
- **Límite por operación:** el monto no puede superar `PAYMENT_LIMIT_UVB × UVB_VALUE_CENTS`. Si lo supera, la API responde `422`; en un pago inter, además, devuelve un pacs.002 `RJCT`.
- **Sin efectos parciales:** un rechazo o una falla a mitad del movimiento no deja débito ni crédito, y las entradas del ledger de cada pago suman cero.

Los motivos de devolución, cancelación y rechazo usan los códigos externos públicos de ISO 20022 (por ejemplo `MD06`, `DUPL`, `NOAS`, `AM04`). Los perfiles `pacs.008.001.08`, `pacs.002.001.10`, `pacs.004.001.09`, `camt.056.001.08`, `camt.029.001.09`, `camt.054.001.08` y `pain.002.001.10` usan XSD propios limitados a los campos implementados. **No son los XSD oficiales ni prueban conformidad con ISO 20022 ni con ningún esquema real.**

### Pagos con QR

| Método y ruta | Descripción |
|---|---|
| `POST /qr/static` | QR estático de una llave confirmada; con `amount_cents` es estático con monto fijo |
| `POST /qr/charges` | Cobro dinámico de un solo uso, con referencia y vencimiento (60 a 3.600 segundos) |
| `GET /qr/charges/{charge_id}` | Estado del cobro: `pending`, `reserved`, `paid`, `expired` o `cancelled` |
| `DELETE /qr/charges/{charge_id}` | Anula un cobro pendiente |
| `POST /qr/decode` | Valida el QR y muestra qué se va a pagar, sin mover dinero |
| `POST /qr/pay` | Paga el QR desde una cuenta; elige solo el flujo intra o inter |

Reglas de los pagos con QR:

- **Monto:** en el QR estático lo define quien paga; en el estático con monto fijo y en el dinámico no se puede cambiar (`422`).
- **Un solo uso:** el cobro dinámico se reserva, se paga con `operation_id` `qr-{charge_id}` y queda pagado. Un segundo pago responde `409`; uno vencido o anulado, `410`.
- **Consistencia entre bases:** el cobro (MySQL) y el pago (PostgreSQL) no comparten transacción. Si el pago se rechaza, el cobro vuelve a quedar pendiente; si algo se cae a mitad de camino, la siguiente consulta del cobro lo concilia con la base de pagos.
- **Titular:** un `usuario` solo cobra con llaves de las que es titular; `admin` y `administrador`, con cualquiera.
- **Marcadores de CP:** `{{qr:static}}`, `{{qr:charge}}` y `{{qr:charge-id}}` reutilizan el último QR generado por un CP de la épica.

El formato toma los campos del estándar EMVCo de las entidades administradoras de pagos colombianas. **Supuesto del laboratorio:** usa identificadores propios (`CO.COM.LAB.*`) en lugar de los de las redes reales, el hash de seguridad es un HMAC-SHA256 y el vencimiento vive en la base de cobros. No prueba conformidad con ningún esquema real.

### Casos de prueba

Todo CP ejecutable es una solicitud REST con JSON:

```json
{
  "request_method": "POST",
  "request_path": "/payments",
  "request_query": {},
  "request_headers": {},
  "request_body": {
    "operation_id": "{{op:pago:new}}",
    "source_account_id": "qa-breb-a-origen",
    "destination_key_type": "phone",
    "destination_key_value": "{{key:phone:spbvi-a}}",
    "amount_cents": 125000
  },
  "expected_status_codes": [201],
  "expected_response": {"operation_id": "{{op:pago}}", "status": "completed"}
}
```

El CP aprueba si el código HTTP está entre los esperados, la respuesta contiene los campos de `expected_response` y cumple el JSON Schema que el contrato OpenAPI documenta para ese método, ruta y código. Con `null` en `expected_response`, se validan el código y el contrato. Al ejecutarlo, el sistema reemplaza los marcadores:

| Marcador | Resultado |
|---|---|
| `{{key:new:TIPO}}` | Genera una llave nueva y válida de ese tipo. Si el registro responde 2xx, entra en la lista de llaves de la épica |
| `{{key:TIPO:SPBVI}}` | Toma una llave de esa lista. Por defecto es la más reciente; al ejecutar se puede elegir otra del mismo tipo |
| `{{op:NOMBRE:new}}` / `{{op:NOMBRE}}` | Genera un identificador de operación o reutiliza el último (reenvíos y consultas de estado) |
| `{{secret:NOMBRE}}` | Valor de `QA_SECRET_NOMBRE`; nunca se guarda ni se muestra |

El JSON de cada CP se edita desde **Calidad y pruebas → JSON** o con `PUT /qa/cases/{case_key}`, y cada cambio deja la versión anterior en el historial.

El ejecutor solo admite rutas relativas al destino configurado y no sigue redirecciones. La sesión de quien ejecuta solo se reenvía al mismo host; por eso, en la nube, `QA_TARGET_BASE_URL` apunta al dominio de la Container App.

Ante un corte de conexión o un 502/503/504 que el CP no espera, el ejecutor reintenta hasta tres veces y registra cada intento. El timeout por solicitud es de 30 segundos, para tolerar la reanudación de una base pausada.

Bugs (pantalla **Bugs y fixes**): `abierto → asignado → en_fix → listo_para_retest → cerrado`, o `reabierto` si falla el retest. Tareas: `abierta → en curso → hecha`.

### Mock server de resiliencia

| Método y ruta | Simula |
|---|---|
| `GET /mock/latency?delay_ms=` | Respuesta lenta (hasta 10 s) |
| `GET /mock/upstream?latency_ms=&timeout_ms=` | Servicio aguas arriba lento: si supera el timeout, responde `504` al vencerlo |
| `GET /mock/status/{code}` | Error HTTP (400, 401, 403, 404, 408, 429, 500, 502, 503, 504), con `Retry-After` en 429 y 503 |
| `GET /mock/disconnect` | Corte de conexión a mitad de la respuesta |
| `GET /mock/flaky/{escenario}?failures=&mode=` | Servicio inestable: las primeras N llamadas fallan (503, 502, 504 o corte) y después responde `200` |

### Auditoría

| Método y ruta | Descripción |
|---|---|
| `GET /audit/logs` | Últimas solicitudes, con filtros por `operation_id`, `request_id`, ruta y código (`admin` y `administrador`) |
| `GET /audit/logs/export` | Las mismas solicitudes en CSV |

Cada respuesta lleva `X-Request-ID`. La escritura es asíncrona y por lotes: si la base de logs no responde, la API no se detiene. `/health` no se registra.

### Programa ISO 20022 y plantillas

`qa_programs/iso20022-breb-rest-json.json` es un programa de pruebas ISO 20022 propio, con enfoque API REST / JSON, sobre los endpoints del laboratorio:

- La HU de preparación (`LAB-HU-000`) crea las cuentas y genera una llave de cada tipo.
- pain.001 → `POST /payments`
- pacs.008 / pacs.002 → `POST /payments/inter-spbvi`
- pacs.028 → `GET /payments/{id}`
- camt.052 / camt.053 → `GET /accounts/{id}/statement`
- pacs.004 → `POST /payments/{id}/returns`
- camt.056 / camt.029 → `POST /payments/{id}/cancellation-requests` y `POST /investigations/{id}/resolution`
- camt.054 → `GET /accounts/{id}/notifications`
- pain.002 → `GET /payments/{id}/status-report`
- Resiliencia → `/mock/*`

El programa se regenera con `python qa_programs/build_iso20022_breb.py`.

En [`qa_programs/plantillas/`](qa_programs/plantillas/README.md) hay plantillas para cada pieza. El `admin` usa la de épica. Las de programa, HU, CP, tarea, bug y fix sirven para el resto de la documentación.

## Pruebas

No requieren bases reales ni envían correos: usan SQLite en memoria, `mongomock` y un emisor de correo falso.

```powershell
python -m pytest                          # backend
cd web; npm run build; npm run test:e2e   # compilación y E2E con Playwright
```

- **Backend:** cubre las pruebas de aceptación de la fase 1 y el resto del sistema.
  - Fase 1: llaves duplicadas en paralelo, rechazos sin efectos, idempotencia, rollback, ledger que suma cero y cero consultas al DICE en pagos intra.
  - Llaves, pagos inter con ISO 20022, autenticación y MFA, roles y gestión QA.
  - Plantillas validadas contra los esquemas.
  - Devoluciones, cancelaciones, investigaciones, notificaciones y pain.002, con sus XML validados.
  - Validación de contrato, mock server y registro de auditoría.
  - Programa ISO 20022 completo: 60 CP en dos rondas contra un servidor real, con llaves nuevas en cada una.
  - Pagos con QR: formato EMVCo y CRC, QR alterados, cobro de un solo uso, reserva, rechazo que libera el cobro, vencimiento, anulación y conciliación entre bases.
- **E2E:** levantan una API aislada (puerto 8010) y Vite (5174). Recorren MFA, ejecución y edición de un CP, ciclo de llaves, pagos intra e inter con devolución, cancelación y notificaciones, auditoría, el ciclo completo de un bug y los pagos con QR (lectura por texto, imagen y cámara simulada), en anchos de 320 a 1280 px. También comprueban que la app es instalable (manifiesto, íconos y service worker), que abre sin conexión, que no guarda respuestas de la API y que no hace consultas con la app oculta.

## Correos

Todos los correos (código MFA, bienvenida y avisos QA) usan una sola plantilla (`app/core/email_templates.py`):

- **Formato:** HTML con CSS inline y una columna adaptable, más una alternativa en texto plano.
- **Sin recursos externos:** no usan JavaScript, imágenes, fuentes ni hojas de estilo externas.
- **Contenido:** nunca incluyen credenciales, tokens ni datos de pagos. La única excepción es el código MFA, que se muestra con su vencimiento y el aviso de no compartirlo.

Salen por la API HTTP de Brevo y el remitente (`BREVO_SENDER_EMAIL`) debe estar verificado:

- **Código MFA y bienvenida:** salen directo desde la API.
- **Avisos QA:** cuando `NOTIFICATIONS_QUEUE_URL` está configurada, la API los encola en AWS SQS y la Lambda `qalabspbvi-notifier` los entrega. La Lambda lee la API key de Brevo desde SSM Parameter Store.

Cada aviso indica quién hizo la acción; en las ejecuciones dice "Ejecutado por" con el nombre y el correo.

Los avisos QA llegan a todos los integrantes de la épica, incluido quien hizo la acción. Las importaciones y actualizaciones de programas envían un solo resumen.

Si Brevo falla, la acción queda guardada y el aviso se reintenta con `POST /qa/notifications/retry`.

## Despliegue

El laboratorio reparte sus componentes entre varias nubes, cada uno con su base dedicada (ver [Plataformas](#plataformas)).

**Las bases duermen sin uso y se activan al entrar:**

- La API escala a cero y no usa pool de conexiones en la nube (`DATABASE_POOL=null`): no quedan sesiones abiertas que mantengan despiertas a Azure SQL (pausa a los 60 minutos) o a Neon (suspensión a los 5 minutos).
- `/health` no toca ninguna base, así que los monitores no las despiertan.
- La interfaz y la app instalada no trabajan en segundo plano: el estado de un cobro QR solo se consulta con la pantalla visible y hasta su vencimiento.
- `/health/databases` (requiere sesión) despierta y comprueba las bases: responde `503` mientras alguna se reanuda y `200` cuando todas están listas. La interfaz lo consulta al entrar y habilita la ejecución de CP cuando están listas, y el ejecutor lo usa antes de cada ejecución.
- Al abrir la aplicación con sesión válida, `/auth/me` despierta DIFE y DICE en segundo plano. Mientras Azure SQL se reanuda (cerca de un minuto), la conexión se reintenta, y si aún no está lista la API responde `503` con "La base de datos se está activando".

El despliegue es continuo:

1. Cada push a `main` ejecuta en GitHub Actions las pruebas del backend, la compilación de React y los E2E.
2. Si pasan, se construyen las imágenes de la API y del gateway ISO 20022, etiquetadas con el commit, y se publican en GHCR.
   - El build falla si al driver ODBC le falta alguna librería.
   - Antes de seguir se comprueba que la imagen se descarga sin credenciales.
3. Se crea una revisión nueva de la Container App, se publica el código de la Lambda y se despliega el gateway en Render.
4. Vercel publica la interfaz por su integración con el repositorio.
5. Al final se comprueba que la API responde por la URL pública y que el sitio publicado es la compilación.
6. Con el laboratorio desplegado, la misma imagen pasa a **producción**, un entorno separado con sus propias bases, cola de avisos y registro de solicitudes. Se publica automáticamente solo desde `main` y solo si el laboratorio se desplegó bien; la interfaz de producción se publica desde una rama propia que solo escribe ese paso.

GitHub entra a Azure y AWS por OIDC con permisos mínimos, sin contraseñas guardadas en el repositorio. En AWS, el rol de GitHub solo puede actualizar el código de la Lambda.

La infraestructura está en `infra/azure/main.bicep` y `infra/aws/notifications.yaml` y se aprovisiona por CLI.

### Supuestos

Supuestos del laboratorio, que se ajustan si cambian las reglas:

- Tipos y orden de los mensajes ISO 20022.
- Formatos de llave.
- Valor de la UVB.
- Mapeo de pain.001, pacs.028 y camt.052/053 a los endpoints.
- Perfil del QR: identificadores propios, hash HMAC-SHA256 y vencimiento fuera del QR.

## Autor

**Luis Felipe Arias Carriazo**
[GitHub](https://github.com/lariasca1994) · [LinkedIn](https://linkedin.com/in/lfac1)

## Licencia

MIT
