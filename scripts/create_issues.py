"""Crea las etiquetas y los issues del proyecto en GitHub usando gh CLI.

Uso (desde la raiz del repo, con `gh auth login` hecho y el remoto creado):
    python scripts/create_issues.py --dry-run   # solo muestra lo que haria
    python scripts/create_issues.py             # crea etiquetas e issues

Es idempotente: si ya existe un issue con el mismo titulo, lo salta.
Se escribe en Python (no en PowerShell) para evitar problemas de
codificacion con tildes en Windows PowerShell 5.1.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

# (nombre, color hex sin '#', descripcion)
LABELS = [
    ("bug", "d73a4a", "Algo produce un resultado incorrecto"),
    ("enhancement", "a2eeef", "Funcionalidad nueva o mejora"),
    ("documentation", "0075ca", "Documentación"),
    ("P0", "b60205", "Imprescindible para la entrega"),
    ("P1", "d93f0b", "Importante: mejora mucho la evaluación"),
    ("P2", "fbca04", "Deseable si hay tiempo"),
    ("area: reglas", "1d76db", "Motor de reglas deterministas"),
    ("area: ia", "5319e7", "Extracción y análisis con LLM"),
    ("area: notion", "0e8a16", "Integración con Notion"),
    ("area: api", "006b75", "Servicio web FastAPI"),
    ("area: deploy", "0052cc", "Despliegue y enlace público"),
    ("area: infra", "c5def5", "Estructura del repo, dependencias y configuración"),
    ("area: tests", "bfd4f2", "Pruebas y CI"),
]

ISSUES = [
    {
        "title": "Reestructurar el repo como paquete preauth/ y cargar configuración sin romper imports",
        "labels": ["enhancement", "P0", "area: infra"],
        "body": """
## Contexto
Los módulos están sueltos en la raíz y `notion_sync.py` lee `os.environ[...]` al importarse: cualquier módulo que lo importe (por ejemplo la app web) falla con `KeyError` si falta una variable. Hay imports a mitad de archivo e imports duplicados.

## Tareas
- [ ] Crear `preauth/__init__.py` y mover con `git mv` (conserva el historial): `agente_preauth.py` -> `preauth/reglas.py`, `notion_sync.py` -> `preauth/notion_repo.py`, `llm_local.py` + `extraer_desde_texto` -> `preauth/extraccion.py`.
- [ ] Crear `preauth/config.py` que cargue `.env` con `python-dotenv` y valide cada variable solo cuando se use.
- [ ] Crear el cliente de Notion dentro de una función, no a nivel de módulo.
- [ ] Mover todos los imports al inicio de cada archivo.
- [ ] Actualizar los imports de `app/main.py`.
- [ ] Fijar versiones exactas en `requirements.txt` con las que muestra `pip list` en el entorno que funciona.

## Criterios de aceptación
- `python -c "import preauth.reglas, preauth.notion_repo"` funciona sin ninguna variable de entorno.
- `uvicorn app.main:app` arranca sin `NOTION_TOKEN` definido.
""",
    },
    {
        "title": "Esquema de Notion como única fuente de verdad (nombres de propiedades y estados)",
        "labels": ["bug", "P0", "area: notion"],
        "body": """
## Contexto
- El autofill escribe `costo estimado` (con espacio) pero `InformeMedico` lee `costo_estimado`: el costo queda en 0 y la regla de monto **siempre pasa**.
- El código escribe `estado = Listo`, el README documenta `procesado`. La API no crea opciones nuevas en una propiedad Status, así que un nombre inexistente hace fallar la actualización.
- `get2()` con nombres alternativos oculta estas diferencias en lugar de corregirlas.

## Tareas
- [ ] Crear `preauth/esquema.py` con constantes para cada propiedad y cada valor de estado y decisión.
- [ ] Renombrar las propiedades en Notion con una sola convención (snake_case).
- [ ] Crear `scripts/verificar_esquema.py`: lee el esquema real de cada data source y reporta propiedades faltantes o con tipo distinto.
- [ ] Eliminar `get2()` y los nombres alternativos.
- [ ] Actualizar las tablas del README.

## Criterios de aceptación
- `python scripts/verificar_esquema.py` termina sin diferencias.
- Ningún nombre de propiedad aparece escrito fuera de `esquema.py`.
""",
    },
    {
        "title": "Normalizar texto (acentos, mayúsculas, espacios) en procedimientos, documentos y urgencia",
        "labels": ["bug", "P0", "area: reglas"],
        "body": """
## Contexto
`.lower()` no elimina acentos: "ecografía" no coincide con la palabra clave "ecografia", y "Colecistectomia" (sin tilde) se **deniega** como no cubierto. `urgencia` se compara con `"emergencia"` exacto, así que "Emergencia" se trata como programada.

## Tareas
- [ ] Función `norm()`: `unicodedata.normalize("NFKD")`, quitar diacríticos, minúsculas, `strip()` y colapsar espacios.
- [ ] Comparar siempre normalizado en ambos lados: cobertura, exclusiones, carencia por procedimiento, documentos y urgencia.
- [ ] Mostrar al usuario el nombre canónico (con tilde), no el normalizado.

## Criterios de aceptación
Tests que pasan con "Colecistectomia", "COLECISTECTOMÍA", "  colecistectomía  " y "Emergencia".
""",
    },
    {
        "title": "Fechas: hoy se evalúa al importar y los meses de afiliación ignoran el día",
        "labels": ["bug", "P0", "area: reglas"],
        "body": """
## Contexto
- `def evaluar(..., hoy=date.today())` fija la fecha al importar el módulo. En un servidor que corre varios días, la carencia deja de avanzar.
- `meses_afiliado` cuenta 1 mes entre el 31 de enero y el 1 de febrero.

## Tareas
- [ ] Firma `hoy: date | None = None` y dentro `hoy = hoy or date.today()`.
- [ ] Restar un mes si `hoy.day < fecha_inicio.day`; nunca devolver negativos.
- [ ] Póliza con fecha de inicio futura -> hallazgo para revisión manual.
- [ ] Documentar en el README la convención elegida para fin de mes.

## Criterios de aceptación
Tests: 31-ene -> 1-feb = 0 meses; 15-ene -> 15-sep = 8 meses; póliza con fecha futura.
""",
    },
    {
        "title": "Motor de reglas: acumular hallazgos y agregar el estado REVISION_MANUAL",
        "labels": ["enhancement", "P0", "area: reglas"],
        "body": """
## Contexto
El motor corta en el primer fallo y devuelve un solo motivo. Datos faltantes se convierten en `DENEGADA` (procedimiento vacío -> "no cubierto"), un costo 0 pasa la regla de monto y `faltantes` sale de un `set` sin orden.

## Tareas
- [ ] Agregar `Decision.REVISION_MANUAL`.
- [ ] `evaluar()` evalúa todas las reglas y devuelve una lista de hallazgos `{regla, resultado, mensaje}`.
- [ ] Precedencia de la decisión final: DENEGADA > REVISION_MANUAL > DOCUMENTOS_FALTANTES > PREAPROBADA.
- [ ] Procedimiento vacío o desconocido -> REVISION_MANUAL (nunca DENEGADA).
- [ ] Costo <= 0 -> falta `presupuesto_hospital`.
- [ ] `faltantes` ordenado.
- [ ] Emergencia: registrar un hallazgo informativo para revisión posterior.
- [ ] En Notion: nueva opción en el select `decision` y propiedad `hallazgos` (rich text) en Resoluciones.

## Criterios de aceptación
- Un caso con carencia incumplida y documentos faltantes reporta ambos hallazgos.
- Dos ejecuciones con la misma entrada producen exactamente la misma salida.
""",
    },
    {
        "title": "Extractor con IA: OpenAI con salida estructurada y proveedor configurable",
        "labels": ["enhancement", "P0", "area: ia"],
        "body": """
## Contexto
- `llm_local.py` no está conectado al flujo: `notion_sync.py` sigue usando regex.
- Depende de Ollama en `localhost`, que no existe en el servidor público, y un modelo de 20B no cabe en una instancia gratuita.
- El docstring dice `llama3.2:3b` pero el valor por defecto es `gpt-oss:20b`.
- Limpiar los bloques de código a mano es frágil y el timeout de 180 s contradice el objetivo de menos de 10 s.
- La urgencia por palabra clave marca "no es urgente" como emergencia (y eso salta la carencia).

## Tareas
- [ ] Modelo Pydantic `ExtraccionInforme` con `Literal` para procedimiento (lista cerrada + "desconocido"), documentos y urgencia; campos `cie10`, `costo_estimado`, `documentos_aportados`, `documentos_pendientes`, `evidencia` (citas cortas del texto) y `confianza` (0 a 1).
- [ ] Variable `LLM_PROVIDER=openai|ollama|regex`. OpenAI con `client.responses.parse(..., text_format=ExtraccionInforme)` y modelo en `OPENAI_MODEL`.
- [ ] Ollama: pasar el JSON schema en el parámetro `format` en lugar de limpiar el texto a mano (confirmar en la documentación de Ollama).
- [ ] Timeout <= 20 s. Si falla, usar regex, pero la urgencia solo sale del campo explícito, nunca del texto.
- [ ] Limitar el tamaño del texto de entrada.
- [ ] Confianza por debajo de un umbral -> REVISION_MANUAL.
- [ ] La IA nunca decide: solo extrae. `evaluar()` decide.

## Criterios de aceptación
- 10 informes sintéticos en `tests/data/` con resultado esperado (incluye "no es urgente", tildes y un informe sin costo).
- Precisión de extracción y latencia p50/p95 medidas y anotadas en el README.
""",
    },
    {
        "title": "IA: verificar coherencia entre diagnóstico CIE-10 y procedimiento",
        "labels": ["enhancement", "P1", "area: ia"],
        "body": """
## Contexto
`diagnostico_cie10` se recoge y nunca se usa. Verificar que el diagnóstico justifica el procedimiento es donde la IA aporta más valor real.

## Tareas
- [ ] Campos `coherente: bool` y `justificacion: str` en la extracción (o una segunda llamada).
- [ ] Incoherente -> hallazgo que lleva a REVISION_MANUAL con la justificación. Nunca DENEGADA por esta vía.
- [ ] Mostrar la justificación en Resoluciones y en la página demo.

## Criterios de aceptación
K80 + colecistectomía -> coherente. Z41 + colecistectomía -> revisión manual con justificación legible.
""",
    },
    {
        "title": "Integración con Notion robusta: data_source_id, paginación, reintentos e idempotencia",
        "labels": ["enhancement", "P1", "area: notion"],
        "body": """
## Contexto
`q()` captura cualquier excepción, no hay paginación (máximo 100 resultados por consulta), se cargan todas las pólizas en cada ejecución, no se manejan los 429 y un fallo entre crear la resolución y actualizar el estado produce resoluciones duplicadas.

## Tareas
- [ ] Resolver los tres `data_source_id` una vez al arrancar; capturar solo `APIResponseError`.
- [ ] Paginación con `has_more`/`next_cursor` (o el helper de paginación de `notion-client`).
- [ ] Consultar la póliza filtrando por `paciente_id` en lugar de cargarlas todas.
- [ ] Reintentos con backoff respetando `Retry-After` ante HTTP 429.
- [ ] Relación `informe` en Resoluciones; antes de crear, comprobar si ya existe una para ese informe.
- [ ] Procesar cada caso en su propio `try/except` para que un fallo no detenga el resto.

## Criterios de aceptación
- Test con respuestas simuladas de 150 pólizas en dos páginas.
- Ejecutar dos veces seguidas no duplica resoluciones.
""",
    },
    {
        "title": "Servicio web: página demo y API de evaluación",
        "labels": ["enhancement", "P0", "area: api"],
        "body": """
## Contexto
El esqueleto actual (`app/main.py`) solo muestra tres casos fijos. Los evaluadores necesitan probar el agente ellos mismos.

## Tareas
- [ ] `POST /api/evaluar`: recibe póliza + informe (estructurado o texto libre) y devuelve decisión, hallazgos, faltantes y latencia.
- [ ] `POST /api/procesar-pendientes`: ejecuta el ciclo de Notion; protegido con `DEMO_ADMIN_TOKEN`.
- [ ] Página `/`: elegir un caso sintético o pegar un informe, ver decisión, motivos, faltantes y latencia.
- [ ] Enlace a las bases de Notion publicadas en modo solo lectura.
- [ ] Modelos Pydantic de request/response para que `/docs` quede documentado.

## Criterios de aceptación
- Tests con `TestClient` para cada endpoint.
- Flujo completo desde la página en menos de 10 s con el servicio despierto.
""",
    },
    {
        "title": "Despliegue público en Render (enlace del agente funcional)",
        "labels": ["enhancement", "P0", "area: deploy"],
        "body": """
## Contexto
El primer entregable es el enlace público del agente funcional. En el plan gratuito de Render el servicio se duerme tras 15 min sin tráfico, tarda alrededor de un minuto en despertar y el sistema de archivos es efímero (el estado vive en Notion, no en disco).

## Tareas
- [ ] Crear el Web Service desde este repositorio (build: `pip install -r requirements.txt`, start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
- [ ] Health check en `/health`.
- [ ] Variables de entorno y secretos solo en Render, nunca en el repo.
- [ ] Fijar la versión de Python igual a la local.
- [ ] Auto-deploy desde `main`.
- [ ] URL en el README y en la descripción del repositorio.
- [ ] Plan para evitar el arranque en frío durante la evaluación.

## Criterios de aceptación
- `/health` responde 200 desde una red externa (por ejemplo, el móvil sin WiFi).
- Tiempos de arranque en frío y en caliente medidos y documentados.
""",
    },
    {
        "title": "Proteger la demo pública contra abuso y costos",
        "labels": ["enhancement", "P0", "area: api"],
        "body": """
## Contexto
El enlace es público y usa nuestra clave de OpenAI y nuestro token de Notion. Cualquiera podría generar costos o llenar Notion de basura.

## Tareas
- [ ] Límite de caracteres en el texto de entrada.
- [ ] Límite simple de solicitudes por IP.
- [ ] Límite de gasto configurado en el proyecto de OpenAI.
- [ ] Endpoints que escriben en Notion solo con `DEMO_ADMIN_TOKEN`.
- [ ] Nunca devolver secretos ni trazas completas en errores.
- [ ] Aviso visible: solo datos sintéticos.

## Criterios de aceptación
- 20 solicitudes seguidas desde la misma IP -> respuesta 429.
- Texto de 50 000 caracteres -> rechazo con mensaje claro.
""",
    },
    {
        "title": "Webhook de Notion con verificación de firma",
        "labels": ["enhancement", "P1", "area: api"],
        "body": """
## Contexto
Hoy no hay disparador: `run_once()` se ejecuta a mano. Notion puede enviar webhooks a un endpoint HTTPS público, firmados con HMAC-SHA256.

## Tareas
- [ ] `POST /webhook/notion`.
- [ ] Handshake: registrar el `verification_token` una vez y guardarlo en `NOTION_WEBHOOK_VERIFICATION_TOKEN`.
- [ ] Verificar `X-Notion-Signature` sobre el cuerpo crudo con `hmac.compare_digest`.
- [ ] Responder 200 rápido y procesar en `BackgroundTasks`.
- [ ] Procesar solo si `estado == pendiente` (evita bucles causados por nuestras propias escrituras) y deduplicar por id de evento.

## Criterios de aceptación
- Firma inválida -> 401.
- Crear un informe pendiente en Notion genera su resolución sin intervención manual.
""",
    },
    {
        "title": "Pruebas automatizadas y CI con GitHub Actions",
        "labels": ["enhancement", "P1", "area: tests"],
        "body": """
## Tareas
- [ ] `pytest` para cada regla y los casos borde de los issues de reglas.
- [ ] Tests del extractor con OpenAI simulado (sin llamadas reales en CI).
- [ ] Tests de la API con `TestClient`.
- [ ] `.github/workflows/ci.yml` que corra en cada push y pull request.
- [ ] Badge de CI en el README.

## Criterios de aceptación
CI en verde en `main` y visible en cada pull request.
""",
    },
    {
        "title": "README final para la entrega",
        "labels": ["documentation", "P1"],
        "body": """
## Tareas
- [ ] Enlace público y cómo probarlo en 2 minutos.
- [ ] Arquitectura real (diagrama actualizado) y por qué la IA extrae y las reglas deciden.
- [ ] Latencias medidas (p50/p95), no estimadas.
- [ ] Limitaciones honestas y trabajo futuro.
- [ ] Datos sintéticos y mención de la Ley 81 de 2019 de protección de datos personales de Panamá.
- [ ] Cómo ejecutar en local.
- [ ] Quitar referencias a números de línea y corregir el ejemplo de "20 meses" (con la fecha de la demo son 32).
""",
    },
    {
        "title": "Reservar el monto usado tras una pre-aprobación",
        "labels": ["enhancement", "P2", "area: reglas"],
        "body": """
## Contexto
`monto_usado` nunca se actualiza: un mismo paciente puede obtener varias pre-aprobaciones que juntas superan el máximo.

## Tareas
- [ ] Al pre-aprobar, sumar `costo_estimado` a un monto reservado de la póliza.
- [ ] Considerar el monto reservado en la regla de monto.
""",
    },
    {
        "title": "(Opcional) Verificar documentos adjuntos reales con IA",
        "labels": ["enhancement", "P2", "area: ia"],
        "body": """
## Contexto
Hoy `documentos` es una lista de casillas: se confía en lo que alguien marcó, no en lo que realmente se adjuntó.

## Tareas
- [ ] Leer la propiedad de archivos del informe en Notion.
- [ ] Clasificar cada archivo por tipo de documento con un modelo con visión o lectura de PDF.
- [ ] Comparar el resultado con los documentos requeridos.
""",
    },
]


def gh(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        ["gh", *args], check=True, text=True, encoding="utf-8", capture_output=capture
    )
    return result.stdout if capture else ""


def existing_titles() -> set[str]:
    out = gh("issue", "list", "--state", "all", "--limit", "500", "--json", "title", capture=True)
    return {item["title"] for item in json.loads(out)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Muestra lo que haria sin crear nada")
    args = parser.parse_args()

    if not args.dry_run and shutil.which("gh") is None:
        print("No se encontró gh. Instálalo con: winget install --id GitHub.cli -e", file=sys.stderr)
        return 1

    for name, color, description in LABELS:
        if args.dry_run:
            print(f"[etiqueta] {name}")
            continue
        gh("label", "create", name, "--color", color, "--description", description, "--force")
        print(f"[etiqueta ok] {name}")

    titles = set() if args.dry_run else existing_titles()
    for issue in ISSUES:
        if issue["title"] in titles:
            print(f"[ya existe] {issue['title']}")
            continue
        if args.dry_run:
            print(f"[issue] {issue['title']}  {issue['labels']}")
            continue
        # El cuerpo va por archivo (UTF-8) para no depender de la codificación de la consola.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
            f.write(issue["body"].strip() + "\n")
            body_path = f.name
        try:
            cmd = ["issue", "create", "--title", issue["title"], "--body-file", body_path]
            for label in issue["labels"]:
                cmd += ["--label", label]
            url = gh(*cmd, capture=True).strip()
            print(f"[issue ok] {url}")
        finally:
            os.remove(body_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
