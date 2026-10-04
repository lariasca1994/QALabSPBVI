# QALabSPBVI

Laboratorio local de pagos inmediatos interoperables y aseguramiento de calidad automatizado. Es un proyecto simulado: no se conecta a infraestructura real de pagos.

## Requisitos

- Python 3.11 o superior.
- MongoDB Community local para la gestion QA; por ahora no requiere Docker.
- Docker Compose solo para levantar PostgreSQL mediante `docker-compose.yml`. No hace falta para ejecutar la aplicacion ni sus pruebas SQL con SQLite.
- La autenticacion real necesita una clave privada y la API de Brevo configurada en `.env`. Los tests usan un emisor de correo falso.
- Los tests usan SQLite y una instancia Mongo simulada aislada (`mongomock`); al ejecutar la aplicacion, la gestion QA se conecta al servicio MongoDB local.

## Desarrollo local sin Docker

Desde PowerShell, en la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m pytest
python -m uvicorn app.main:app --reload
```

La API queda disponible en `http://127.0.0.1:8000` y el chequeo de salud en `http://127.0.0.1:8000/health`.

Pagos y autenticacion usan `DATABASE_URL`, que en local apunta a una base PostgreSQL nueva y exclusiva de QALabSPBVI, nunca a una base existente. Si no hay PostgreSQL disponible, SQLite queda como base temporal (valor por defecto de `.env.example`). Los nombres de variables del `.env` deben coincidir exactamente con los de `.env.example`: la configuracion ignora variables desconocidas, por lo que un nombre distinto deja la conexion en su valor por defecto sin avisar. DIFE y DICE se alojaran en bases independientes nuevas de SQL Server y Oracle, respectivamente. QA usa una base MongoDB nueva y dedicada: no se conecta a `cluster0` ni reutiliza datos de otros proyectos. No se requiere crear una base remota ni compartir credenciales para este esqueleto.

## Registro de llaves en motores independientes

DIFE y DICE son componentes separados con persistencias independientes: la base nueva `QALabDIFE` en SQL Server almacena la asociacion local entre llave, SPBVI y producto; el usuario/esquema nuevo `QALABDICE` en Oracle mantiene el indice global por tipo y valor. No se conectan a bases de otros proyectos. El API orquesta:

1. `POST /difes/{spbvi_id}/keys` recibe tipo, valor y producto de deposito.
2. DICE inserta una reserva `pending`, protegida por una restriccion unica `(key_type, key_value)`.
3. DIFE guarda y activa la asociacion local (`active`) con su propio indice unico.
4. DICE cambia su reserva a `confirmed`.

Son transacciones locales separadas, no una transaccion distribuida. Si un paso falla despues de la reserva, la API responde `503` y conserva `pending`; reenviar la misma solicitud reanuda la activacion. DICE es quien garantiza la unicidad global incluso entre solicitudes simultaneas. Una llave ya confirmada responde `409 Conflict`.

`DIFE_DATABASE_URL` y `DICE_DATABASE_URL` del `.env` apuntan a esas bases dedicadas. La aplicacion crea unicamente las tablas de llaves con este comando idempotente:

```powershell
python -m app.cli init-key-stores
```

Las tablas `dife_keys` y `dife_key_status_events` viven en SQL Server; `dice_keys` vive en Oracle. DICE conserva la referencia al producto de depósito para permitir el enrutamiento, pero no almacena saldos ni transacciones. La migración de `deposit_product_id` agrega la columna a esquemas Oracle existentes y completa referencias confirmadas cuando encuentra la llave activa correspondiente en DIFE.

Los pagos intra-SPBVI resuelven la llave solo en el DIFE del SPBVI con `GET /difes/{spbvi_id}/keys/resolve?key_type=email&key_value=ana%40example.test`; no consultan Oracle/DICE. Los pagos inter-SPBVI consultan DICE mediante `POST /payments/inter-spbvi` y liquidan en un MOL simulado local; no representa una conexión con Banrep ni una liquidación real. Para desplegar sobre instalaciones previas, ejecutá de nuevo `python -m app.cli init-key-stores` para aplicar la columna y recuperar las asociaciones existentes que coincidan entre DIFE y DICE.

Ejemplo de registro:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/difes/spbvi-a/keys -ContentType "application/json" -Body '{"key_type":"email","key_value":"ana@example.test","deposit_product_id":"cuenta-123","owner_email":"titular@example.com"}'
```

Los valores de llave se recortan de espacios en los extremos; el tipo se convierte a minusculas. El valor restante se compara exactamente, sin normalizacion especifica de telefono, correo u otros formatos.

### Ciclo de vida completo de llaves

Cada operación se prueba desde JSON de envío versionado en `tests/fixtures/key_lifecycle/`. Los archivos `create.json`, `delete.json`, `suspend_administrative.json`, `suspend_personal.json`, `reactivate_administrative.json`, `reactivate_personal.json` y `assign_owner.json` se usan directamente en las pruebas HTTP automatizadas.

| Acción | Método y ruta | Permisos / comportamiento |
|---|---|---|
| Crear | `POST /difes/{spbvi_id}/keys` | `admin` o `administrador`. `owner_email` es opcional; la llave se reserva en DICE y se activa en DIFE. |
| Asignar titular a una llave existente | `PATCH /difes/{spbvi_id}/keys/owner` | `admin` o `administrador`. Necesario para habilitar la gestión personal de llaves legacy creadas sin titular. |
| Suspender administrativamente | `POST /difes/{spbvi_id}/keys/suspend` con `"suspension_type":"administrative"` | `admin` o `administrador`; registra motivo y actor. |
| Suspender personalmente | `POST /difes/{spbvi_id}/keys/suspend` con `"suspension_type":"personal"` | Requiere sesión autenticada cuyo correo coincida con el titular registrado. |
| Reactivar administrativamente | `POST /difes/{spbvi_id}/keys/reactivate` con `"reactivation_type":"administrative"` | `admin` o `administrador`; puede reactivar cualquiera de los dos tipos de suspensión. |
| Reactivar personalmente | `POST /difes/{spbvi_id}/keys/reactivate` con `"reactivation_type":"personal"` | Solo el titular y únicamente si la suspensión fue personal. |
| Eliminar | `DELETE /difes/{spbvi_id}/keys` con JSON de motivo | `admin` o `administrador`; libera la llave en los dos índices y conserva una auditoría no enlazada a la fila eliminada. El alta posterior de la misma llave vuelve a estar disponible. |

Ejemplos de cuerpos JSON de envío:

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "deposit_product_id": "cuenta-123",
  "owner_email": "titular@example.com"
}
```

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "reason": "Bloqueo preventivo tras revisión",
  "suspension_type": "administrative"
}
```

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "reason": "Solicitud del titular",
  "suspension_type": "personal"
}
```

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "reason": "Revisión administrativa completada",
  "reactivation_type": "administrative"
}
```

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "reason": "El titular confirma la reactivación",
  "reactivation_type": "personal"
}
```

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "reason": "Retiro de la llave solicitado",
  "owner_email": "titular@example.com"
}
```

```json
{
  "key_type": "email",
  "key_value": "ana@example.test",
  "reason": "Solicitud de eliminación de prueba"
}
```

La suspensión escribe primero `pending` en DICE para que la llave deje de resolverse en pagos inter-SPBVI; luego actualiza DIFE y confirma el estado final en DICE. La reactivación sigue el mismo patrón para no reabrir el enrutamiento global antes de activar DIFE. Si una operación entre motores falla, responde `503`; reenviar el mismo JSON completa la transición sin duplicar el evento de auditoría. La eliminación es física en los directorios para liberar la unicidad global, pero su snapshot (tipo/valor, titular, producto, motivo, usuario, rol, acción y fecha) queda en `key_lifecycle_events` de DIFE.

Las llaves legacy no tienen titular por defecto. Un `admin` o `administrador` debe asignarlo con `PATCH /difes/{spbvi_id}/keys/owner` antes de aceptar una suspensión/reactivación personal. El correo del titular no se devuelve en la API de resolución. Las mutaciones requieren sesión autenticada y CSRF, igual que las demás rutas protegidas.

## Acceso seguro y roles

El backend usa tres roles (`admin`, `administrador`, `usuario`): el admin crea epicas; el administrador crea HU, otros elementos y usuarios, y puede asociar integrantes a epicas; los usuarios ejecutan CP/tareas y registran bugs/fixes. El admin tambien puede asociar integrantes y crear administradores para el arranque, pero no puede asignar el rol admin por API. La creacion de usuarios comunes queda en manos del administrador.

El acceso requiere contrasena y un codigo MFA de seis digitos enviado por correo. Las contrasenas se almacenan con Argon2id, el codigo vence a los cinco minutos, tiene maximo cinco intentos y solo se puede usar una vez. Hay limitacion de intentos de contrasena/correo y de envios MFA. Las sesiones usan tokens aleatorios opacos, guardados como digest en SQL, revocables en logout, con vencimiento por inactividad (30 minutos) y absoluto (8 horas). La cookie de sesion es `HttpOnly`, `SameSite=Strict` y `Secure` fuera del modo local; las operaciones que cambian estado requieren token CSRF.

El correo como segundo factor es la decision del MVP, no equivale a una llave FIDO/passkey ni a una app TOTP y depende de proteger bien la cuenta de correo. El modo local HTTP deja `Secure` desactivado solo para desarrollo; nunca publiques ese modo.

El servicio MongoDB se mantiene local, ligado a localhost, hasta configurar su autenticacion. No abras el puerto MongoDB a la red ni lo uses con datos sensibles compartidos antes de habilitar autenticacion/usuarios; MongoDB local no sustituye el cifrado en reposo ni los controles del sistema operativo.

### Bootstrap y configuracion privada

1. Copia `.env.example` a `.env`.
2. Genera una clave privada desde PowerShell:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Guarda el resultado solo como `AUTH_SECRET_KEY` dentro de `.env`; no lo pegues en el chat ni en Git.
3. Configura `BREVO_API_KEY`, `BREVO_SENDER_EMAIL` y, opcionalmente, `BREVO_SENDER_NAME` en `.env`. La clave es una credencial secreta; no la compartas en el chat ni la agregues a Git. La integracion usa la API HTTPS oficial de Brevo y nunca registra la clave ni el cuerpo privado de la respuesta del proveedor.
4. Crea el primer admin en una base vacia. El comando solicita el correo y la contrasena de forma interactiva, y solo funciona mientras no existan usuarios:

   ```powershell
   python -m app.cli create-initial-admin
   ```

   No hay usuario, contrasena ni clave MFA por defecto. El admin inicial puede crear administradores; un administrador puede crear usuarios y administradores. Los usuarios autentican con `/auth/login`, obtienen el codigo por correo y completan MFA en `/auth/verify-email-code`. Antes de cada POST desde una interfaz, solicita `/auth/csrf` y envia el token recibido en `X-CSRF-Token`. La cookie de sesion no se expone a JavaScript.

   Para crear una cuenta administrativa adicional y visible, ejecutá `python -m app.cli create-admin`. Solicita correo, nombre y contraseña nueva de forma interactiva, almacena solo el hash Argon2id y rechaza duplicados; no uses contraseñas que hayan sido compartidas en chats.

## Gestion QA local en MongoDB

El servicio `MongoDB` se instala y ejecuta en este equipo; el backend usa `MONGODB_URL` y `MONGODB_DATABASE` de `.env.example`. Los documentos de epicas, HU, CP, tareas, ejecuciones, bugs y fixes se guardan en la base Mongo local nueva y dedicada `qalabspbvi_qa`. No apuntes la URI a `cluster0` ni a una base con datos de otros proyectos. Usuarios, hashes de contrasena y sesiones siguen en la base SQL exclusiva de QALabSPBVI.

Permisos:

- Solo `admin` crea epicas.
- `admin` y `administrador` pueden asociar integrantes. El creador queda incluido automaticamente.
- `administrador` crea HU, CP, tareas y usuarios.
- `usuario` miembro de la epica ejecuta casos/tareas y registra bugs/fixes.
- `administrador` gestiona asignacion y transiciones de bugs.

Un CP almacena su contrato HTTP (metodo, ruta local, query, cabeceras, body, estados y respuesta esperados) junto con sus criterios Jira. Al invocar `POST /qa/cases/{case_key}/execute`, se ejecuta esa definicion contra `QA_TARGET_BASE_URL`; se compara la respuesta con los estados y el JSON esperado, y se guarda metodo, URL, request/response redactados, resultado y duracion.

El runner solo permite rutas relativas, metodos HTTP permitidos y un destino base configurado por el operador; en modo local obliga a usar `localhost`, no sigue redirecciones y no acepta URLs absolutas. Los campos con nombres de credenciales se rechazan salvo referencias declarativas como `{{secret:PAYMENTS_API_TOKEN}}`, resueltas solo desde `QA_SECRET_PAYMENTS_API_TOKEN` en el entorno o en `.env` y nunca guardadas en la ejecucion. Las cabeceras sensibles, por ejemplo `Authorization`, solo aceptan referencias a secretos. La cookie de sesion y el CSRF del ejecutor se reenvian unicamente cuando el host destino coincide exactamente con el host de la solicitud entrante. La respuesta registrada tambien se limpia de esos valores y se omite si supera 1 MB.

La gestion de bug sigue `open → assigned → in_fix → ready_for_retest → closed`, o `reopened` si el retest falla. Al asignar, el responsable debe ser integrante de la epica; quien implementa el fix lo marca listo para retest y el administrador mueve el bug a retest. El cierre exige el resultado aprobado; si falla, se reabre y se puede registrar otro fix.

Cada alta, ejecucion o cambio relevante crea el aviso dentro del mismo documento Mongo que la accion y lo envia por la API HTTPS de Brevo a los integrantes de la epica, incluyendo al autor. Si Brevo no acepta el envio, la accion queda guardada y el aviso se conserva como pendiente; `POST /qa/notifications/retry` permite reintentar con rol `admin` o `administrador`. Los avisos pendientes se eliminan del documento al entregarse para evitar crecimiento sin limite; por eso Mongo no funciona como historial de correos. La entrega es de mejor esfuerzo y al menos una vez: si la conexion cae despues de que Brevo acepta el correo, podria recibirse duplicado.

El listado de usuarios activos para asociarlos a epicas esta disponible en `GET /auth/users` para `admin` y `administrador`; el listado de bugs/fixes de una epica se obtiene en `GET /qa/epics/{epic_key}/bugs`. Las respuestas de Mongo incluyen solo la epica a la que pertenece el usuario autenticado.

## Cuentas y pagos intra-SPBVI

El endpoint local `POST /accounts` crea una cuenta simulada con `account_id`, `spbvi_id` y `balance_cents`. El saldo inicial se registra como una entrada de apertura en el ledger; los montos siempre son enteros en centavos.

`POST /payments` recibe `operation_id`, `source_account_id`, `destination_key_type`, `destination_key_value` y `amount_cents`. Resuelve el destino exclusivamente mediante el DIFE del SPBVI de origen; no consulta el resolvedor inter-SPBVI del DICE. La transferencia crea un pago y dos entradas de ledger balanceadas en la misma transaccion que actualiza ambos saldos.

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/accounts -ContentType "application/json" -Body '{"account_id":"origen","spbvi_id":"spbvi-a","balance_cents":5000}'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/payments -ContentType "application/json" -Body '{"operation_id":"op-001","source_account_id":"origen","destination_key_type":"email","destination_key_value":"ana@example.test","amount_cents":1250}'
```

Un destino inexistente responde `404`; saldo insuficiente e identificador reutilizado con datos distintos responden `409`. La repeticion exacta de un `operation_id` devuelve el pago original (`200`) sin volver a mover dinero. Los montos cero, negativos o no enteros se rechazan con `422`. Si falla cualquier escritura durante el movimiento, se revierten tanto los saldos como los registros del pago.

### Pagos inter-SPBVI con MOL simulado

El flujo inter-SPBVI usa `POST /payments/inter-spbvi` con el mismo contrato JSON que `/payments`. DICE resuelve la llave confirmada, la referencia de producto debe corresponder a una cuenta local cuyo `spbvi_id` coincida con el participante receptor, y el MOL simulado debita y acredita en una unica transaccion PostgreSQL con dos entradas de ledger balanceadas. Tras la liquidacion, la respuesta incluye `payment_type: "inter_spbvi"`, el XML de orden `pacs008_xml` y la confirmacion `pacs002_xml` con estado `ACCP`. Los dos mensajes se generan con el timestamp del pago y se validan con los XSD locales. Un replay exacto conserva idempotencia y devuelve los mismos XML; un mismo `operation_id` no se puede reutilizar entre flujos intra e inter.

Llave o cuenta destino ausente responde `404`; fondos insuficientes devuelve `409` con `status: "rejected"` y un `pacs002_xml` `RJCT` (sin pago ni movimientos de ledger persistidos); conflicto de idempotencia responde `409`; intento de usar el flujo inter con una llave del mismo SPBVI, `422`; y una falla del almacenamiento DICE o del MOL simulado, `503`. Una falla despues de escrituras parciales del MOL revierte pago, ledger y ambos saldos. El perfil y las fixtures contemplan `ACCP`, `RJCT` y `PDNG`; `PDNG` solo se valida como mensaje de prueba, porque el MOL actual liquida sincronicamente y todavia no mantiene pagos pendientes en el flujo de ejecucion. Las pruebas usan motores SQLite aislados y no envian transacciones a las bases locales configuradas.

### Adaptador ISO 20022 provisional

`app.domains.iso20022.messages` genera y valida los perfiles de laboratorio `pacs.008.001.08` y `pacs.002.001.10`; el endpoint inter-SPBVI usa el adaptador tras liquidar el pago. El adaptador recibe datos neutrales del pago, conserva los importes como enteros en centavos y serializa el XML con escape seguro. La validacion usa XSD locales propios (`*-lab.xsd`), que cubren solo los campos usados por este laboratorio: **no son los XSD oficiales de ISO 20022 ni prueban conformidad con Banrep**. Versiones, campos obligatorios y secuencia del flujo siguen siendo supuestos hasta revisar el anexo 6 de la Circular DSP-465. El contrato JSON enviado por las pruebas está en `tests/fixtures/payments/inter_spbvi.json`.

## Con Docker Compose

En un equipo con Docker operativo, `docker compose up --build` levanta la API y PostgreSQL local. Usa una base PostgreSQL nueva y dedicada para `DATABASE_URL`; no apuntes el contenedor a datos preexistentes. Las credenciales definidas en Compose son exclusivamente de desarrollo local y no deben reutilizarse en ningun entorno compartido o productivo.

## Interfaz web local

La primera interfaz está en `web/` con React, TypeScript y Vite; usa tipografía del sistema y recursos locales, sin cargar fuentes ni servicios externos. Para trabajarla localmente, inicia FastAPI en una terminal y luego, desde `web/`, ejecuta:

```powershell
npm install
npm run dev
```

Abrí `http://127.0.0.1:5173`. El proxy de Vite reenvía `/api` al backend local en `http://127.0.0.1:8000`, sin requerir CORS ni configurar un segundo origen. La interfaz React cubre login MFA, resumen, ejecución de casos QA, usuarios autorizados, alta/consulta y ciclo de vida de llaves, creación de cuentas de laboratorio y pagos intra/inter-SPBVI. Las pantallas de llaves y pagos usan los contratos HTTP existentes; las cuentas y llaves se identifican por sus IDs/valores, ya que la API aún no ofrece endpoints de listado. Las cookies de sesión siguen siendo `HttpOnly`; el frontend solo conserva la preferencia de tema claro/oscuro. La navegación se adapta a pantallas pequeñas y la compilación se valida con `npm run build`.

### Pruebas E2E de escritorio y móvil

Con Playwright instalado, ejecutá desde `web/`:

```powershell
npm run test:e2e
```

La suite inicia una API aislada en `8010` y Vite en `5174`; no usa el `.env` ni las bases locales configuradas para desarrollo. El servidor de pruebas genera credenciales temporales, MFA de correo simulado, SQLite para pagos/DIFE/DICE y Mongo simulado, y elimina su carpeta temporal al terminar. Incluye autenticación + ejecución QA a 320 px, ciclo de llaves a 375 px y pagos intra/inter a 320–375 px. Requiere que el entorno Python activo tenga las dependencias del proyecto y `mongomock`; para seleccionar explícitamente el intérprete, definí `QALAB_E2E_PYTHON` antes de ejecutar el comando. No se envían correos ni se escriben registros en las bases locales reales.

La paleta es sobria y toma como referencia el carácter institucional de Banrep; no se presenta como reproducción de una guía oficial de marca. El tema claro/oscuro es accesible desde el botón de tema y respeta la preferencia del sistema como valor inicial.

Las cuentas no se siembran automáticamente ni existen usuarios privilegiados ocultos. El primer `admin` se crea mediante `python -m app.cli create-initial-admin`; una cuenta adicional con rol `admin` se crea interactivamente mediante `python -m app.cli create-admin`. La interfaz lista las cuentas activas y permite crear roles que autoriza el backend. El endpoint `/auth/users` no asigna el rol superior `admin` y esa restricción se valida en el servidor.

## Estructura actual

```text
app/
  api/       Rutas HTTP y autenticacion
  core/      Configuracion, seguridad y correo
  db/        Base SQLAlchemy y sesiones
  domains/
    auth/    Credenciales, MFA y sesiones
    keys/    Registro, auditoria y ciclo de vida DIFE/DICE
    payments/ Transferencias intra/inter-SPBVI, MOL simulado y ledger
    iso20022/ Adaptador XML provisional y perfiles XSD locales
    qa/      Gestion Jira, runner HTTP, Mongo y notificaciones
  main.py    Fabrica y punto de entrada FastAPI
tests/       Pruebas automaticas
web/
  src/       Interfaz React/TypeScript y sistema visual claro/oscuro
```

El backend local incluye pagos intra e inter-SPBVI con MOL simulado y emisión de mensajes ISO 20022 de laboratorio, el ciclo completo de llaves, autenticacion MFA y la gestion QA descrita arriba. La primera versión de la interfaz web ya funciona localmente; el despliegue cloud sigue pendiente y requiere confirmación explícita. El archivo de configuracion de ejemplo no contiene credenciales reales; las pruebas no envian correo ni requieren conectar una base cloud.

## Integracion continua en GitHub

El workflow `.github/workflows/ci.yml` ejecuta en cada push y pull request las pruebas del backend, la compilación TypeScript/React y los recorridos E2E con Chromium. También se puede iniciar manualmente desde GitHub Actions. Los E2E usan persistencias temporales y simuladas; el workflow no requiere ni configura secretos o conexiones a bases cloud.

El job `Desplegar laboratorio` está preparado, pero queda omitido por defecto: solo se activa en un `push` a `main` si `LAB_DEPLOY_ENABLED` vale `true` en GitHub. Antes de activarlo hay que aprovisionar y revisar la infraestructura, configurar el environment protegido `lab`, el principal OIDC de Azure y sus permisos mínimos, las variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `LAB_ACR_NAME`, `LAB_ACR_LOGIN_SERVER`, `LAB_RESOURCE_GROUP`, `LAB_CONTAINER_APP_NAME`, `LAB_PUBLIC_APP_URL`, y el secreto `LAB_STATIC_WEB_APPS_DEPLOYMENT_TOKEN`. No cargues valores de `.env` a GitHub.

La configuración del job por sí sola no habilita despliegues: `LAB_DEPLOY_ENABLED` no está activado. Producción no tiene todavía un job de publicación; requiere el mismo artefacto validado, un environment `prod` protegido con aprobación y una aprobación específica antes de incorporarlo.

El borrador de infraestructura está en `infra/azure/` y el plan en `.azure/plan.copilotmd`. No ejecutes Bicep ni actives el CD hasta revisar y aprobar la infraestructura y completar la configuración de PostgreSQL/Neon, Atlas, Oracle/OCI, el correo MFA y DIFE. El Dockerfile actual tampoco instala todavía el controlador ODBC del sistema requerido para que `pyodbc` se conecte a Azure SQL; esto debe corregirse y validarse en local antes de desplegar.
