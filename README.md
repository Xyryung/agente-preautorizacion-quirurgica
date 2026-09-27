# Agente de Pre-Autorización Quirúrgica en Tiempo Real

[![CI](https://github.com/Xyryung/agente-preautorizacion-quirurgica/actions/workflows/ci.yml/badge.svg)](https://github.com/Xyryung/agente-preautorizacion-quirurgica/actions/workflows/ci.yml)

**Demo pública:** https://agente-preautorizacion.onrender.com

> Todos los datos de la demo son **sintéticos**. No ingreses datos reales de pacientes.

## Pruébalo en 2 minutos

<!-- Actualizar cuando entre #9 (formulario para pegar un informe y POST /api/evaluar). -->

1. Abre https://agente-preautorizacion.onrender.com. Si nadie la usó en un rato, la primera carga puede tardar ~30 s mientras el servidor gratuito despierta; después responde en menos de medio segundo.
2. La página evalúa en vivo tres casos sintéticos y muestra para cada uno la **decisión**, el **motivo**, los **documentos faltantes** y la **latencia**:
   - Documentación completa → `PREAPROBADA`
   - Faltan documentos → `SOLICITUD_DOCUMENTOS_FALTANTES`
   - Procedimiento no cubierto → `DENEGADA`
3. El mismo resultado en JSON está en [`/api/demo`](https://agente-preautorizacion.onrender.com/api/demo).
4. La documentación interactiva de la API está en [`/docs`](https://agente-preautorizacion.onrender.com/docs).

## 1. Resumen ejecutivo
Sistema que elimina la espera de horas/días en la autorización de cirugías. Recibe el **informe médico digital (Hospital)** y la **póliza (Aseguradora)** desde **Notion**, los cruza con reglas de negocio y emite en segundos: `PREAPROBADA`, `SOLICITUD_DOCUMENTOS_FALTANTES` o `DENEGADA`.

**Objetivo:** pasar de un proceso manual (24-72h) a uno automático (<10s por caso).

## 2. Arquitectura

```
┌──────────────┐      ┌──────────────┐
│ Hospital     │      │ Aseguradora  │
│ (informe)    │      │ (póliza)     │
└──────┬───────┘      └──────┬───────┘
       │                     │
       ▼                     ▼
┌────────────────────────────────────┐
│ Notion DB                          │
│ - Informes_Hospital (estado)       │
│ - Pólizas_Aseguradora              │
│ - Resoluciones                     │
└──────────────┬─────────────────────┘
               │ polling 60s / webhook
               ▼
┌────────────────────────────────────┐
│ Agente Python (preauth/reglas.py)  │
│ 1. Cobertura  2. Carencia          │
│ 3. Monto      4. Documentos        │
└──────────────┬─────────────────────┘
               ▼
┌────────────────────────────────────┐
│ Respuesta en Notion + notificación │
│ AUT-ID, motivo, faltantes          │
└────────────────────────────────────┘
```

## 3. Modelo de datos en Notion

### 3.1 DB `Informes_Hospital`
Nombres y tipos según `preauth/esquema.py` (única fuente de verdad, snake_case).
| Propiedad | Tipo Notion | Ejemplo |
|---|---|---|
| paciente_id | Title | P001 |
| procedimiento | Select | Colecistectomía |
| diagnostico_cie10 | Rich text | K80 |
| medico | Rich text | Dr. Gil |
| urgencia | Select: `programada`, `emergencia` | programada |
| documentos | Multi-select | identificacion, informe_medico, consentimiento, ecografia_abdominal, analitica, presupuesto_hospital |
| costo_estimado | Number | 8000 |
| estado | Status: `pendiente`, `procesado` | pendiente |
| informe_texto | Rich text | Texto libre del informe para autofill |

### 3.2 DB `Pólizas_Aseguradora`
| Propiedad | Tipo | Ejemplo |
|---|---|---|
| paciente_id | Title | P001 |
| cobertura | Multi-select | Colecistectomía, Artroplastia |
| fecha_inicio | Date | 2024-01-01 |
| carencia | Rich text (JSON) | `{"default": 8}` = 8 meses |
| monto_maximo | Number | 50000 |
| monto_usado | Number | 5000 |
| exclusiones | Multi-select | Rinoplastia estética |
| requiere_segunda_opinion | Multi-select | Artroplastia |

### 3.3 DB `Resoluciones`
| Propiedad | Tipo | Descripción |
|---|---|---|
| paciente_id | Title | Enlace al paciente |
| decision | Select (4 valores: `PREAPROBADA`, `SOLICITUD_DOCUMENTOS_FALTANTES`, `REVISION_MANUAL`, `DENEGADA`) | Resultado del agente |
| motivo | Rich text | Explicación legible |
| faltantes | Multi-select | Docs a pedir |
| autorizacion_id | Rich text | `AUT-P001-20260926`, solo si aprobada |
| timestamp | Created time | Auditoría |
| informe | Relation → Informes_Hospital | Idempotencia: una resolución por informe |

## 4. Motor de reglas (`evaluar()` en `preauth/reglas.py`)

`evaluar()` evalúa **todas** las reglas, registra un hallazgo por regla y decide por precedencia. El resultado incluye `decision`, `motivo`, `faltantes`, `hallazgos` y, solo si se pre-aprueba, `autorizacion_id` y `monto_reservado` (el nuevo total reservado). Los textos se comparan sin tildes, mayúsculas ni espacios extra.

### Reglas

1. **Datos**: si el informe no indica un procedimiento reconocible (vacío o `desconocido`) → revisión manual.
2. **Vigencia**: si la póliza inicia después de la fecha de evaluación → revisión manual, y no se evalúa la carencia.
3. **Cobertura**: si el procedimiento no está en `cobertura_procedimientos` o está en `exclusiones` → no cumple.
4. **Carencia**: si la urgencia no es `emergencia` y los meses completos de afiliación son menos que la carencia exigida (la del procedimiento o la `default`) → no cumple. Ej.: la póliza exige 8 meses y el paciente lleva 5 → no cumple. En emergencia se omite la carencia y queda un hallazgo informativo para revisión posterior.
5. **Monto**: sin costo estimado (0 o menos) → falta `presupuesto_hospital`. Si `monto_usado + monto_reservado + costo_estimado > monto_maximo` → no cumple. `monto_reservado` suma los costos ya pre-aprobados y aún no liquidados; al pre-aprobar, el costo se suma a `monto_usado` de la póliza en Notion (#15).
6. **Documentos**: requeridos = `DOCS_BASE` + los del procedimiento (o `default`) + `segunda_opinion` si la póliza lo exige. Los que no están adjuntos se informan en `faltantes`, en orden alfabético.

### Decisión final (precedencia)

| Si algún hallazgo es… | Decisión |
|---|---|
| No cumple | `DENEGADA` |
| Requiere revisión | `REVISION_MANUAL` |
| Faltan documentos | `SOLICITUD_DOCUMENTOS_FALTANTES` |
| Ninguno de los anteriores | `PREAPROBADA` |

Si la decisión es `DENEGADA`, `faltantes` queda vacío: una denegación no se corrige con documentos. Todos los hallazgos quedan en `hallazgos` para auditoría.

### Cómo se cuentan los meses de carencia

Se cuentan meses completos: un mes se cumple el mismo día del mes siguiente. Si ese día no existe (póliza iniciada el 31 y un mes con menos días), se cumple el último día de ese mes; por ejemplo, del 31 de enero al 28 de febrero hay un mes. Una póliza que inicia después de la fecha de evaluación no está vigente: el caso pasa a revisión manual y no se evalúa la carencia.

## 5. Extracción con IA

El informe en texto libre se convierte en datos estructurados con la API de OpenAI, usando salida estructurada con esquema estricto: el modelo solo puede elegir procedimientos, documentos y urgencias de nuestras listas. La IA **extrae**; las reglas deterministas **deciden**.

- Cada valor viene con `evidencia`: una cita breve del informe que lo justifica, para auditar la extracción.
- Si la llamada falla (tiempo límite de 20 s, sin clave, error de la API), se usa un extractor por expresiones regulares y el resultado indica que se usó el respaldo. Sin IA, la urgencia nunca se infiere del texto.
- Se envía con `store=False` y solo con datos sintéticos. Enviar datos reales de pacientes a una API externa requeriría la base legal correspondiente (Ley 81 de 2019).
- Configuración: `OPENAI_MODEL=gpt-5-mini`, `OPENAI_REASONING_EFFORT=minimal` (ver `.env.example`).

### Precisión y latencia medidas

10 informes sintéticos (`tests/data/informes_sinteticos.json`), una ejecución por configuración. Reproducir con `python -m scripts.evaluar_extraccion --proveedor openai`.

| Configuración | Procedimiento | Urgencia | Costo | CIE-10 | Documentos aportados | Latencia p50 | p95 | Máx. |
|---|---|---|---|---|---|---|---|---|
| **`gpt-5-mini`, razonamiento `minimal` (elegida)** | 10/10 | 10/10 | 10/10 | 10/10 | 9/10 | 3,0 s | 4,5 s | 4,9 s |
| `gpt-5-mini`, razonamiento `low` | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 6,5 s | 13,5 s | 17,3 s |
| Sin IA (expresiones regulares) | 10/10 | 2/10 | 8/10 | 10/10 | 4/10 | < 1 ms | < 1 ms | < 1 ms |

Se eligió `minimal`: con `low` se evita el único error (S10: marcar como aportado el presupuesto porque el informe menciona su monto), pero la latencia se duplica y el máximo (17,3 s) queda cerca del tiempo límite de 20 s, que haría caer el caso al respaldo sin IA. Con 10 informes y una ejecución por configuración, la diferencia de un campo es indicativa, no concluyente.

### Señales para revisión manual

La regla `extraccion` envía el caso a `REVISION_MANUAL` si:

- **la confianza que reporta el modelo es menor que 0,7**, o si se usó el respaldo sin IA (confianza 0); o
- **alguna cita de `evidencia` no aparece en el informe** (señal de que el modelo inventó texto).

Medición con `gpt-5-mini`, razonamiento `minimal`:

| Informe | Confianza | Campos con error | Citas encontradas en el texto |
|---|---|---|---|
| S01 | 0,95 | 0 | 7/7 |
| S02 | 0,90 | 0 | 8/8 |
| S03 | 0,95 | 0 | 7/7 |
| S04 | 0,90 | 0 | 4/4 |
| S05 | 0,90 | 0 | 7/7 |
| S06 | 0,90 | 0 | 7/7 |
| S07 | 0,90 | 0 | 6/6 |
| S08 | 0,90 | 0 | 6/6 |
| S09 | 0,60 | 0 | 2/2 |
| S10 | 0,90 | 1 | 7/7 |

**Limitación:** ninguna de las dos señales detectó el único error medido (S10). La confianza del informe con error (0,90) fue igual a la de los informes correctos: la confianza refleja cuánta información hay (el informe vacío, S09, obtuvo 0,60), no si la interpretación es correcta. Las citas fueron todas textuales (61/61), así que no hubo falsas alarmas, pero S10 no inventó texto: interpretó mal una frase real. Para esos casos, `evidencia` muestra al revisor la frase exacta que se usó. El umbral de 0,7 es una red de seguridad elegida con 10 informes, no una calibración.

## 6. Cómo ejecutar en local

Requisitos: Python 3.14 (la misma versión que usan Render y CI) y Git.

```bash
git clone https://github.com/Xyryung/agente-preautorizacion-quirurgica.git
cd agente-preautorizacion-quirurgica

# Entorno virtual
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Dependencias (versiones fijadas) + herramientas de pruebas
pip install -r requirements-dev.txt

# Variables de entorno: copia la plantilla y completa solo lo que vayas a usar.
# La app arranca sin ninguna variable; cada grupo se valida cuando se usa.
cp .env.example .env
```

| Qué | Comando |
|---|---|
| Servicio web (página + API) | `uvicorn app.main:app --reload` → http://localhost:8000 |
| Pruebas | `pytest` |
| Motor de reglas con los casos de ejemplo, sin Notion | `python -m preauth.reglas` |
| Un ciclo contra Notion (requiere las variables `NOTION_*`) | `python -m preauth.notion_repo` |

El despliegue en Render (configuración, tiempos medidos y keep-alive) está documentado en [`docs/despliegue.md`](docs/despliegue.md).

### Integración Notion (pseudocódigo listo)
```python
from notion_client import Client
import os
notion = Client(auth=os.environ["NOTION_TOKEN"])
# 1. Leer pendientes
resp = notion.databases.query(database_id=os.environ["NOTION_DB_INFORMES"],
    filter={"property": "estado", "select": {"equals": "pendiente"}})
# 2. Por cada página: mapear a Poliza/InformeMedico -> evaluar() ->
#    notion.pages.create(db_resoluciones, properties={...}) +
#    notion.pages.update(page_id, properties={"estado": "procesado"})
```

## 7. Ejemplos

| Caso | Entrada | Salida |
|---|---|---|
| Aprobado | P001, Colecistectomía, 32 meses afiliado (póliza desde 01/01/2024, evaluada el 26/09/2026), docs completos, $8000/$50000 | `PREAPROBADA`, `AUT-P001-20260926` |
| Faltan docs | Solo `identificacion + informe_medico` | `SOLICITUD_DOCUMENTOS_FALTANTES`, faltan `consentimiento, ecografia_abdominal, analitica` |
| No cubierto | Rinoplastia estética | `DENEGADA`, no cubierto |
| Carencia | 5 meses afiliado, exige 8 | `DENEGADA`, `No cumple carencia: 5/8 meses` |
| Emergencia | Igual anterior pero `urgencia=emergencia` | Salta carencia, sigue a monto/documentos |
| Póliza futura | Póliza que inicia después de la fecha de evaluación | `REVISION_MANUAL`, no se evalúa la carencia |

## 8. Rendimiento medido

Medido con `curl` desde Panamá contra el servicio en Render (detalle en [`docs/despliegue.md`](docs/despliegue.md)):

| Escenario | Tiempo |
|---|---|
| Servicio despierto, n = 20 | p50 **0,28 s** · p95 **0,36 s** |
| Motor de reglas (dentro del servidor) | < 0,05 ms por caso |
| Arranque en frío (plan gratuito, tras 15 min sin tráfico) | 32,6 s, mitigado con un keep-alive cada 10 min |
- Extracción con IA (`gpt-5-mini`, razonamiento `minimal`): p50 3,0 s, p95 4,5 s por informe; detalle en la sección de extracción.

## 9. Datos sintéticos y privacidad

- Todos los pacientes, pólizas e informes del repositorio, de Notion y de la demo son **ficticios**.
- La información de salud es un **dato sensible** según la **Ley 81 de 2019 de protección de datos personales de Panamá** (reglamentada por el Decreto Ejecutivo 285 de 2021). Por eso la demo pública muestra un aviso y no debe recibir datos reales.
- Un uso real requeriría, como mínimo: consentimiento del titular, cifrado en tránsito y en reposo, control de acceso, registro de auditoría y un acuerdo de tratamiento con cada proveedor externo (Notion, OpenAI).
- Los secretos (tokens de Notion y OpenAI) viven solo en variables de entorno de Render, nunca en el repositorio.

## 10. Limitaciones y trabajo futuro
- Matching de `procedimiento` es por string exacto → normalizar a códigos CPT/CIE o usar embeddings/LLM para informe libre.
- Sin OCR/PDF: hoy `documentos` es checklist; integrar OCR para verificar contenido real.
- Sin autenticación, auditoría HIPAA/GDPR ni reintentos: añadir log inmutable, cifrado y cola con retries.
- **Plan gratuito de Render:** 0,1 CPU y 512 MB; el servicio se duerme tras 15 min sin tráfico (keep-alive con GitHub Actions, que puede retrasarse unos minutos) y el disco es efímero, por eso el estado vive en Notion.
- **Despliegue manual:** Render no despliega solo al hacer merge a `main`; hay que usar *Manual Deploy* (el CI ya puede llamar a un Deploy Hook si se configura el secret).
- **Protección de la demo:** el límite de solicitudes por IP vive en memoria, así que se reinicia con cada deploy y solo sirve para una instancia.
- Evolución IA: usar LLM solo para extraer `procedimiento/CIE/documentos` del informe en lenguaje natural, manteniendo las 4 reglas deterministas para la decisión (explicable y auditable).

## Herramientas de IA utilizadas

### En el producto
| Herramienta | Uso |
|---|---|
| OpenAI API (modelo configurado en OPENAI_MODEL) | Extracción estructurada del informe médico y verificación de coherencia diagnóstico-procedimiento. La IA extrae; las reglas deterministas deciden. |

### En el desarrollo
| Herramienta | Uso | Cómo se verificó |
|---|---|---|
| Claude (Anthropic) | Revisión de código, planificación del backlog, esqueleto del servicio web y script de issues | Pruebas locales, revisión en PR |
| Claude (Anthropic) | Pruebas y CI con GitHub Actions, despliegue en Render, protección de la demo pública y este README | Tests en CI, prueba manual en local y contra el servicio en Render |
