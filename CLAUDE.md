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
- Backend: un solo host. Recomendado Azure Container Apps (el entorno laboratorio-pagos-env ya existe en el grupo PersonalEAN, eastus). Alternativa: Cloud Run en el proyecto prpagos de GCP.
- Plataforma QA (HU, casos tipo Jira, JSON, ejecuciones, solicitudes, respuestas y evidencias): base MongoDB nueva, limpia y dedicada a QALabSPBVI; no reutilizar el cluster `cluster0` ni bases de otros proyectos.
- Oracle Autonomous DB en OCI queda como posibilidad separada, únicamente mediante una base nueva, limpia y aislada; nunca reutilizar una base existente. No es el repositorio canónico de los artefactos QA mientras MongoDB cumpla ese rol.
- Logs: Postgres en Neon (falta confirmar que sirva).
- Cola de eventos: SQS en la cuenta AWS actual, us-east-1.
- Frontend: React + TypeScript + Vite en `web/`, solo local durante la fase actual. Evaluar despliegue después de estabilizar y validar la experiencia local.
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
El núcleo local de llaves (alta, suspensión/reactivación personal y administrativa, eliminación, asignación de titular y auditoría), pagos intra/inter-SPBVI con MOL simulado, autenticación y backend QA con ejecución de CP JSON está implementado. Los cuerpos JSON del ciclo de llaves se mantienen en `tests/fixtures/key_lifecycle/` y se ejecutan en pruebas HTTP automatizadas. La interfaz React local en `web/` incluye login MFA, resumen, ejecución QA, usuarios por rol, operaciones de llaves y pagos; la API aún no ofrece listados de cuentas o llaves, por lo que esas pantallas requieren los identificadores y valores conocidos. Falta validar los flujos autenticados de extremo a extremo con Playwright.

1. Crear y validar bases locales limpias y dedicadas para SQL Server/DIFE, Oracle/DICE, PostgreSQL/pagos-autenticación y MongoDB/QA; no reutilizar datos existentes.
2. Completar la coordinación idempotente y recuperable entre DIFE y DICE y los smoke tests locales.
3. Ampliar la cobertura ISO 20022 con JSON de prueba de respuesta/rechazo y resultados del MOL, preservando los contratos XML actuales.
4. Validar con Playwright los flujos autenticados de la interfaz React/TypeScript, incluidos llaves, pagos, QA, MFA y viewports móviles; evaluar endpoints de listado para cuentas y llaves.
5. Tras estabilizar y validar localmente, desplegar en la nube acordada usando bases nuevas y vacías, con confirmación explícita antes de aprovisionar recursos.

La interfaz web local incluye login MFA, resumen de salud, consulta/ejecución de casos QA, gestión de usuarios autorizada por rol, operaciones de llaves, pagos y temas claro/oscuro. Usa la paleta institucional como referencia, sin afirmar que reproduce colores oficiales; no carga fuentes externas. Se ejecuta aparte de FastAPI con `cd web; npm install; npm run dev` en Windows/PowerShell. La autenticación conserva la sesión en cookie HttpOnly y las rutas sensibles siguen protegidas en el backend. El layout adaptable admite viewports desde 320 px; la verificación visual automatizada hasta ahora cubrió la pantalla de acceso, no las vistas autenticadas. No sembrar administradores ocultos ni credenciales predeterminadas: el admin inicial y las cuentas admin adicionales se crean interactivamente con los comandos CLI documentados en README.

Diagrama de referencia de los flujos intra e inter: https://claude.ai/artifact/VzbGmnH3VLvvogB9hwDjJW (privado; si no abre, no es crítico).