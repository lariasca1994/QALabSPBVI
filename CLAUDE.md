# 1) Instalar Claude Code (instalador nativo; requiere cuenta Pro, Max, Team o Console)
irm https://claude.ai/install.ps1 | iex

# 2) Cerrá y abrí una terminal nueva, y verificá
claude --version

# 3) Crear la carpeta del proyecto
mkdir A:\Github\QALabSPBVI
cd A:\Github\QALabSPBVI
git init

# 4) Guardá el archivo CLAUDE.md (el siguiente bloque) dentro de esta carpeta

# 5) Arrancar Claude Code (la primera vez abre el navegador para iniciar sesión)
claude

# QALabSPBVI

Laboratorio simulado de pagos inmediatos interoperables, inspirado en Bre-B (Banco de la República de Colombia), para practicar y mostrar aseguramiento de calidad automatizado: pruebas de API, de mensajería ISO 20022 y E2E. Es un proyecto de portafolio. No se conecta a ninguna infraestructura real de pagos.

Idioma: respondé y comentá el código en español. Tuteá con voseo.
Texto del producto (interfaz, correos, mensajes de la API, README y documentación): español neutro con tuteo ("prueba", no "probá"). El voseo es solo para responder en el chat.

## Reglas de trabajo
- Cambios pequeños y verificables: escribí la prueba, corré la prueba, y recién ahí avanzá.
- Siempre desarrollar y validar primero en local; adaptar e integrar servicios cloud después de estabilizar el núcleo local.
- Cada componente debe usar una base de datos nueva, vacía, dedicada y aislada. No reutilizar bases, esquemas, colecciones ni datos de otros proyectos o instalaciones existentes, aunque pertenezcan al mismo motor o cluster. Esto aplica a DIFE, DICE, pagos/autenticación y QA, tanto en local como en cloud.
- Nunca pongas contraseñas, llaves ni tokens en el repo. Van en un .env local (ignorado por git) o en el gestor de secretos de cada nube. Dejá un .env.example sin valores reales.
- No crees ni modifiques recursos en la nube sin confirmármelo antes. Cuando se aprovisione, se hace por CLI, no por consola web.
- Mis comandos corren en Windows con PowerShell 5.1: sin placeholders con < >, y cuidado con comillas dobles dentro de --query.
- Dinero siempre en enteros (centavos), nunca float.

## Dominio (Bre-B), lo que está confirmado en documentos públicos de Banrep
- ISO 20022 como lenguaje común; las entidades y sistemas se conectan por APIs.
- SPBVI: sistema de pagos de bajo valor inmediato de cada participante. Cada SPBVI tiene su DIFE (directorio federado de llaves).
- DICE: directorio centralizado de llaves, administrado por Banrep. Solo resuelve llaves, no guarda datos transaccionales.
- Intra-SPBVI (pagador y receptor en el mismo SPBVI): la llave se resuelve en el DIFE, sin pasar por el DICE; liquida el propio SPBVI.
- Inter-SPBVI: la llave se resuelve en el DICE; liquida el MOL (mecanismo operativo de liquidación) con cuentas en Banrep.
- La confirmación al SPBVI de origen llega en máximo 20 segundos. Límite de 1.000 UVB por operación (en el lab es un parámetro configurable).
- Unicidad de llaves: una llave apunta a un solo producto de depósito; un producto puede tener varias llaves. El DICE valida la unicidad ANTES de confirmar el registro.

## Supuestos del laboratorio (marcar como supuesto en código y docs)
- Los tipos de mensaje concretos están en el anexo 6 de la Circular DSP-465 del Banrep, que no tengo. Hasta tenerlo se asume: pacs.008 (orden) y pacs.002 (estado) entre SPBVI; pain.001 solo en el tramo app a SPBVI y es opcional. La consulta de llaves es una API propia, mapeable a la familia prxy si el anexo la exige.
- El adaptador ISO 20022 local usa provisionalmente los perfiles `pacs.008.001.08` y `pacs.002.001.10` con XSD propios limitados a los campos implementados. No son esquemas oficiales y no deben describirse como conformes a ISO 20022 o Banrep; reemplazarlos/ajustarlos al recibir el anexo y XSD aplicables.
- El orden exacto de los mensajes en el flujo inter-SPBVI es supuesto.
- El núcleo del dominio no debe depender del formato de mensaje: el gateway ISO 20022 es un adaptador que se agrega después.

## Registro de llaves (diseño acordado)
DIFE pregunta al DICE si la llave está libre; el DICE la reserva como PENDING; el DIFE la activa; el DICE la pasa a CONFIRMED. En el MVP monolítico el DICE es un índice único (tipo + valor) verificado en la misma transacción. Nunca deben existir llaves duplicadas, ni siquiera con registros simultáneos.

El ciclo de vida de llaves incluye suspensión administrativa y personal, reactivación administrativa y personal, y eliminación con auditoría. Solo admin/administrador ejecutan acciones administrativas y eliminación; las acciones personales requieren que el usuario autenticado sea el titular asociado. Las llaves existentes sin titular requieren asignación administrativa antes de habilitar acciones personales. Los cambios DIFE/DICE no son una transacción distribuida: primero poner DICE en PENDING, completar DIFE y confirmar el estado final en DICE; si falla, la misma solicitud debe poder reintentarse sin duplicar auditoría. La eliminación libera ambos índices únicos, pero persiste una copia de auditoría en DIFE.

## Arquitectura
Monolito modular en Python (FastAPI + SQLAlchemy 2), con dominios separados y listos para volverse servicios: DIFE (varios SPBVI), DICE, orquestador de pagos, ledger por SPBVI, MOL simulado y gateway ISO 20022. Plataforma QA con pruebas pytest e interfaz local React/TypeScript en `web/`; validar journeys de navegador con Playwright antes de completarla. PostgreSQL es la base local prevista y se define en Docker Compose; si Docker no está disponible, se permite SQLite temporal para desarrollar y ejecutar pruebas localmente. GitHub Actions para CI.

Pagos inter-SPBVI: `POST /payments/inter-spbvi` resuelve llaves confirmadas en DICE y liquida mediante el MOL simulado en una transacción local PostgreSQL. DICE conserva una referencia al producto de depósito para enrutar; no guarda saldos ni transacciones. Los pagos intra siguen consultando únicamente DIFE. El MOL es una simulación, no una conexión ni liquidación real de Banrep.

El pago inter-SPBVI genera y valida `pacs.008.001.08` y `pacs.002.001.10` de laboratorio y los devuelve como `pacs008_xml`/`pacs002_xml`. Los XSD locales son perfiles propios incompletos, no oficiales ni evidencia de conformidad. Mantener el dominio de pagos independiente del formato y sustituir/ampliar perfiles cuando estén disponibles los requisitos del anexo 6.

### Plataforma de QA y casos tipo Jira
- Las historias de usuario (HU) y los casos de prueba se modelan con una estructura familiar a Jira: identificador, título, descripción, prioridad, precondiciones, pasos, datos y resultado esperado, además de relaciones entre HU, casos y ejecuciones.
- Los casos deben ser automatizables. Al seleccionar y ejecutar un caso desde la plataforma, el sistema resuelve su definición/datos JSON asociados y ejecuta el flujo automáticamente; no alcanza con mostrar el JSON o describir pasos manuales.
- La ejecución debe mostrar claramente el método HTTP usado por cada solicitud (GET, POST, PUT, DELETE, etc.), su URL, datos relevantes de solicitud, código/estado y respuesta, sin exponer secretos.
- Persistir los artefactos QA (HU, casos, JSON, ejecuciones, solicitudes/respuestas y evidencias) en una base MongoDB nueva, vacía, exclusiva de este proyecto. No conectar `cluster0` ni reutilizar datos de otros proyectos.
- Mantener persistencias independientes: DIFE en una base SQL Server nueva; DICE en un usuario/esquema Oracle nuevo; pagos/autenticación en PostgreSQL nuevo (SQLite solo temporal para pruebas locales); QA en MongoDB nuevo. No compartir ni reutilizar bases existentes. Coordinar DIFE y DICE mediante operaciones idempotentes y recuperables, no simulando una transacción distribuida.
- No usar una base Oracle existente. Si se decide usar Oracle para algún componente, deberá ser una base nueva, limpia y aislada; no se reutilizan bases preexistentes.
- Autenticación con los roles `admin`, `administrador` y `usuario`: solo `admin` crea épicas y administradores; `admin` y `administrador` crean usuarios, asocian miembros a épicas y crean/asignan tareas; `administrador` crea HU y CP; `usuario` gestiona CP (edita su JSON y los ejecuta), tareas, bugs y fixes. Los CP ejecutables son solo API REST con JSON salvo que se decida ampliar.
- Exigir contraseña segura y segundo factor enviado por correo (sin aplicación autenticadora en esta etapa); las sesiones y permisos deben validarse en el backend, no solo ocultando controles en la interfaz.
- Enviar notificaciones por correo por ejecuciones, creación de CP, bugs/fixes y cambios relevantes de gestión a todos los miembros asociados a la épica, incluido quien originó la acción. Se usa la API de Brevo; la API key y los datos del remitente se configuran en `.env` local o en el gestor de secretos, nunca en el repositorio.
- Gestionar bugs/fixes con flujo inicial: `abierto → asignado → en_fix → listo_para_retest → cerrado`; si falla el retest, volver a `reabierto`. El fix queda vinculado al bug y solo se cierra tras retest aprobado.

### Experiencia visual
- La interfaz web debe tener una presentación profesional, sobria y apropiada para un producto financiero/QA; no usar una paleta excesivamente colorida.
- Incluir temas claro y oscuro, con colores institucionales de Banrep como referencia para acentos y estados, manteniendo contraste y accesibilidad. Verificar los tonos exactos contra una guía o recurso oficial antes de tratarlos como colores de marca confirmados.
- Los correos transaccionales (MFA, ejecuciones y notificaciones de gestión) deben mantener el mismo lenguaje visual: profesional, sobrio, limpio y consistente con la identidad de QALabSPBVI, con acentos discretos inspirados en Banrep y contraste accesible.
- El HTML de correo debe usar CSS inline y componentes compatibles con clientes de correo; no depender de JavaScript, hojas externas, fuentes remotas ni imágenes externas. Diseñar un layout responsive de una columna, legible en móvil y escritorio, que se adapte razonablemente a tema claro/oscuro y conserve una alternativa de texto plano.
- Nunca incluir credenciales, tokens de sesión, secretos, datos sensibles de pagos ni contenido privado de respuestas en el correo. El código MFA es la excepción funcional: mostrar solo el código de un uso y su vencimiento, con aviso de no compartirlo.

## Infraestructura prevista (se conecta DESPUES del núcleo)
- Backend: Azure Container Apps (rg-qalabspbvi-lab, eastus2), imagen pública en GHCR, escala a cero. GCP quedó descartado (exige facturación); el proyecto qalabsspbvi se eliminó.
- Plataforma QA (HU, casos tipo Jira, JSON, ejecuciones, solicitudes, respuestas y evidencias): base MongoDB nueva, limpia y dedicada a QALabSPBVI; no reutilizar el cluster `cluster0` ni bases de otros proyectos.
- Oracle Autonomous DB en OCI queda como posibilidad separada, únicamente mediante una base nueva, limpia y aislada; nunca reutilizar una base existente. No es el repositorio canónico de los artefactos QA mientras MongoDB cumpla ese rol.
- Cobros QR: TiDB Cloud (compatible con MySQL, AWS us-east-1), clusters Starter qalabspbvi-qr-lab (base qalab_qr) y qalabspbvi-qr-prod (base qalab_qr_prod), cada uno con usuario propio ({prefijo}.qalab_qr), límite de gasto mensual 0 y escala a cero. URL en el secreto qr-database-url de cada Key Vault → QR_DATABASE_URL; credenciales y clave root solo en .env.lab/.env.prod. La API exige TLS hacia TiDB y en la nube rechaza SQLite para los cobros. Las claves de la API de TiDB Cloud (TIDB_PUBLIC_KEY/TIDB_PRIVATE_KEY) están solo en .env.
- Logs: proyecto Neon QALabSPBVI-logs (base qalab_logs para lab, qalab_logs_prod para prod). Middleware ASGI puro (app/domains/audit), escritura asíncrona por lotes, X-Request-ID, GET /audit/logs y CSV. /health no se registra.
- Cola de avisos QA: SQS + DLQ y Lambda `qalabspbvi-notifier` en AWS us-east-1 (infra/aws/notifications.yaml, implementado). La API solo envía a la cola con un usuario IAM limitado; el MFA sigue directo por Brevo.
- Gateway ISO 20022 (services/iso20022_gateway) en Render (plan gratuito, sin tarjeta): Cloud Run quedó descartado porque exige facturación activa. La API cae al adaptador en proceso si el gateway no responde o está suspendido.
- Frontend: React + TypeScript + Vite en `web/`, publicado en Vercel con proxy /api a la Container App: producción en qalabspbvi-prod.vercel.app (la URL pública de la demo) y laboratorio en qalabspbvi.vercel.app.
- Proyectos propios para reutilizar ideas: gestor-casos-qa, qa-evidencia (CodeBuild + Playwright + correo), verificador-api.

## Fase 1 (empezar aquí): intra-SPBVI de punta a punta, sin nubes
1. Esqueleto: FastAPI, SQLAlchemy 2, pytest, docker-compose con Postgres, .env.example, .gitignore, README.
2. Ledger con transferencia atómica (débito y crédito en una sola transacción; o ambos o ninguno).
3. Registro de llaves DIFE/DICE con unicidad global.
4. Pago intra-SPBVI con idempotencia (mismo identificador de operación no duplica el abono).

Pruebas de aceptación de la fase 1 (todas automáticas):
- Registrar la misma llave dos veces, incluso en paralelo, deja una sola.
- Pago a llave inexistente se rechaza sin tocar ningún saldo.
- Pago con saldo insuficiente se rechaza sin tocar ningún saldo.
- Reenviar la misma orden con el mismo identificador no duplica el abono.
- Si falla algo a mitad del movimiento, no queda ni débito ni crédito.
- Las entradas del ledger de cada pago suman cero.
- En un pago intra el DICE recibe cero consultas.

## Estado y fases siguientes
Implementado y desplegado en el laboratorio multinube (ver README): llaves DIFE/DICE con catálogo de tipos Bre-B y ciclo de vida completo; pagos intra/inter-SPBVI con MOL simulado, límite de 1.000 UVB, consulta de estado y extracto conciliado; ISO 20022 de laboratorio; autenticación con MFA y reenvío; gestión QA (épica → HU → CP, tareas, bugs/fixes, edición versionada del JSON de CP, importación de programas, avisos con plantilla HTML); interfaz React con E2E en Playwright; CI/CD con GitHub Actions a Azure por OIDC.

El programa `qa_programs/iso20022-breb-rest-json.json` (adaptado del documento de pruebas ISO 20022 Bre-B) se importa en una épica y sus 35 CP pasan en la prueba de integración y en el laboratorio. Las llaves de los CP se generan según el tipo Bre-B ({{key:new:TIPO}}) y las transacciones las toman de la lista de llaves de la épica ({{key:TIPO:SPBVI}}). Plantillas en qa_programs/plantillas/.

Implementado además: pacs.004 (devoluciones), camt.056/camt.029 (cancelación e investigación; aceptar devuelve con FOCR), camt.054 (notificaciones por cuenta) y pain.002 (en el pago intra y en /status-report), con XSD propios de laboratorio; validación de contrato JSON Schema (OpenAPI) en cada ejecución de CP; mock server /mock/* y reintentos del ejecutor (3 intentos, timeout 30 s); pantallas de bugs/fixes, operaciones sobre un pago y auditoría. El programa tiene 60 CP y pasa completo en local y en el lab.

Entorno de producción: rg-qalabspbvi-prod (Container App y Key Vault en eastus2; DIFE en servidor Azure SQL propio en australiaeast con oferta gratuita), Neon QALabSPBVI-prod, base qalab_logs_prod, Atlas qalabspbvi_qa_prod (usuario propio), usuario Oracle QALABDICEPROD, stack AWS qalabspbvi-prod-notifications y Vercel qalabspbvi-prod (solo construye la rama production). El job deploy-production del CI corre automáticamente tras el lab (environment production limitado a main, sin aprobación manual) y reescribe la rama production. Credenciales solo en .env.prod (ignorado). Las bases del lab y de producción se verificaron aisladas entre sí.

Red de las bases: sin restricción de IP, porque las IPs de salida de las plataformas son dinámicas (Atlas con 0.0.0.0/0, ADB de DICE con ACL 0.0.0.0/0 y TLS, Azure SQL lab y prod con la regla acceso-abierto, Neon sin lista). La protección es por credenciales propias de cada componente, TLS obligatorio y secretos solo en gestores. Quitar la ACL de la ADB del todo exige mTLS (wallet); por eso se usa 0.0.0.0/0.

Pagos con QR (app/domains/qr, /qr/*): formato EMVCo tomado de "Campos QR Code EMVCo – Estándar de industria EASPBV colombianas" v1.5 (2026): 00, 01 (11 estático / 12 dinámico; 11 con 54 = híbrido), 26 llave Bre-B (01 documento, 02 celular, 03 correo, 04 alfanumérica, 05 código de comercio), 49, 52, 53=170, 54 con punto y dos decimales, 58=CO, 59, 60, 62, 80, 90 id de transacción, 91 hash y 63 CRC-16/CCITT-FALSE. SUPUESTOS: GUI CO.COM.LAB.*, 91 = HMAC-SHA256 con AUTH_SECRET_KEY, sin campos de impuestos y vencimiento en la base de cobros. Cobro dinámico de un solo uso: reservar (UPDATE condicional) → pagar con operation_id qr-{charge_id} (la unicidad de pagos garantiza un solo pago) → marcar pagado; un rechazo libera la reserva y GET del cobro concilia reservas caídas (60 s). Intra si la llave está en el DIFE del SPBVI de origen (sin consultar DICE); si no, inter. Un usuario solo cobra con llaves de las que es titular. Marcadores de CP {{qr:static}}, {{qr:charge}}, {{qr:charge-id}}. /health/databases incluye la base QR.

PWA (web/public: manifest.webmanifest, sw.js, offline.html, icons/): instalable en Android, iOS, Windows, macOS y Linux. El service worker solo guarda la cáscara (index, /assets, íconos) y nunca /api; navegación primero por red. Regla: nada en la web ni en la app mantiene vivas las plataformas (sin background sync, push ni consultas periódicas con la app oculta; el estado de un cobro QR solo se consulta con la pantalla visible y hasta su vencimiento). Las librerías de QR (qrcode, jsqr) se cargan bajo demanda.

Épicas de pruebas (qa_programs/epicas/, EPIC-002 a EPIC-018: 118 HU, 254 CP, 187 tareas): estandarizadas desde los documentos del usuario (los originales se eliminaron: la fuente es qa_programs/epicas/) y cargadas solo en producción (EPIC-00002 a EPIC-00018, tareas repartidas entre las 5 cuentas); verificadas en tests/test_qa_epics.py (importa todas y ejecuta cada CP con el rol de sus precondiciones). Reglas: cada CP indica su rol de ejecución; nada de POST/PUT/PATCH/DELETE sobre /auth/* (la plataforma lo rechaza); ningún CP cierra la sesión, cambia contraseñas, envía correos ni crea épicas, HU o cuentas basura; lo que el ejecutor no puede probar va a criterios o tareas; las pruebas de interfaz (PWA/web) son tareas. Marcadores agregados: {{me:email}}, {{epic:key}} y {{key:last:TIPO:SPBVI}} (las llaves eliminadas quedan en la lista con status deleted). La automatización NO ejecuta CP en la nube: los ejecuta cada usuario.

Latencia de Australia: /health/databases (con sesión) despierta y comprueba las bases; la interfaz lo consulta al entrar y deshabilita Ejecutar mientras tanto, el ejecutor espera hasta 20 s antes de cada CP y qa-evidencia espera ese 200 antes del pago.

BREVO_SENDER_EMAIL (remitente verificado) es variable de entorno de cada Container App, cargada por CLI desde .env.lab/.env.prod y no en los parámetros Bicep: si se reaplica la plantilla, volver a cargarla. En producción estuvo vacía desde su creación hasta el 2026-10-07 (el MFA no podía salir); ya está aplicada.

Avisos por correo: activos en lab y producción (mapeos SQS → Lambda habilitados). Si hace falta pausarlos, se desactiva el mapeo; los mensajes esperan en la cola hasta 4 días. El MFA sale directo por Brevo.

Diagrama de referencia de los flujos intra e inter: https://claude.ai/artifact/VzbGmnH3VLvvogB9hwDjJW (privado; si no abre, no es crítico).