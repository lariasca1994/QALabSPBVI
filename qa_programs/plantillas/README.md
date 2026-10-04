# Plantillas de documentación QA

Plantillas JSON para crear la documentación de una épica en QALabSPBVI. Cada archivo es el cuerpo exacto que espera la API (o el formulario equivalente de **Calidad y pruebas**). Cópialo, cambia los valores y envíalo. Las pruebas de `tests/test_qa_program.py` validan que todas las plantillas cumplan el esquema vigente.

| Plantilla | Quién la usa | Dónde se usa |
| --- | --- | --- |
| `epica.json` | `admin` | **Nueva épica** o `POST /qa/epics`. `member_ids` son los id de los integrantes (ver **Usuarios**). |
| `programa.json` | `administrador` integrante de la épica | **Importar programa** o `POST /qa/epics/{epic_key}/import`. Carga HU, CP y tareas de una vez. |
| `hu.json` | `administrador` | **Nueva HU** o `POST /qa/epics/{epic_key}/stories`. |
| `cp.json` | `administrador` | **+ CP** en una HU o `POST /qa/stories/{story_key}/test-cases`. El `usuario` lo edita después con **JSON**. |
| `tarea.json` | `admin` o `administrador` | **Nueva tarea** o `POST /qa/epics/{epic_key}/tasks`. |
| `bug.json` | Integrante de la épica | `POST /qa/bugs`, a partir de una ejecución fallida (el CP define la épica). |
| `fix.json` | Integrante de la épica | `POST /qa/bugs/{bug_key}/fixes`. |

## Crear una épica nueva con su programa

1. El `admin` crea la épica con `epica.json` y asocia al equipo.
2. Un `administrador` integrante copia `programa.json`, reemplaza las HU, CP y tareas, y lo importa en la épica.
3. Con acceso a las bases, los pasos 1 y 2 se pueden hacer juntos. El bloque `epic` de `programa.json` da el título y la descripción, y todos los usuarios activos quedan como integrantes:

   ```bash
   python -m app.cli seed-program qa_programs/plantillas/programa.json CORREO_ADMIN CORREO_ADMINISTRADOR
   ```

   Si cambias CP que ya están cargados, agrega `--actualizar` (o marca la casilla al importar). Cada CP modificado pasa a una versión nueva y la anterior queda en el historial.

Reglas del programa:

- Cada `ref` es única dentro del archivo.
- Lo que ya existe en la épica con la misma `ref` se omite.
- Si un CP es inválido, se rechaza el programa completo.

## Marcadores dinámicos en los CP

Los CP son solo API REST con JSON. Al ejecutar, el sistema reemplaza estos marcadores en la ruta, la consulta, el cuerpo y la respuesta esperada. Dentro de una ejecución, el mismo marcador da siempre el mismo valor.

| Marcador | Resultado |
| --- | --- |
| `{{key:new:TIPO}}` | Genera una llave nueva y válida del tipo indicado. Si el registro responde 2xx, la llave entra en la **lista de llaves de la épica**. |
| `{{key:TIPO}}` / `{{key:TIPO:SPBVI}}` | Usa una llave de la lista de ese tipo (y SPBVI). Por defecto es la más reciente confirmada; al ejecutar puedes elegir otra de la lista. |
| `{{op:NOMBRE:new}}` | Genera un identificador de operación nuevo y lo guarda en la épica. |
| `{{op:NOMBRE}}` | Reutiliza el último identificador generado con ese nombre (reenvío idempotente, consulta de estado). |
| `{{secret:NOMBRE}}` | Valor de la variable de entorno `QA_SECRET_NOMBRE`. Nunca se guarda ni se muestra. |

Tipos de llave (`TIPO`) y valores que se generan (SUPUESTO: formatos propios del laboratorio):

| Tipo | Valor generado |
| --- | --- |
| `document` | 10 dígitos |
| `phone` | celular de 10 dígitos que inicia en 3 |
| `email` | `qa.xxxxxxxxxx@qalabspbvi.test` |
| `alias` | `@qa` seguido de 10 letras o números |
| `merchant_code` | 8 dígitos |

## Campos comunes

- **Prioridad:** `highest`, `high`, `medium`, `low` o `lowest`.
- **Severidad del bug:** `trivial`, `minor`, `major`, `critical` o `blocker`.
- **`expected_status_codes`:** códigos aceptados.
- **`expected_response`:** subconjunto que debe aparecer en la respuesta; `null` valida solo el código.
- **Credenciales:** no van en campos del JSON. Usa `{{secret:…}}`.
