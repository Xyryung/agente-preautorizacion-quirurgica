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
| Propiedad | Tipo Notion | Ejemplo |
|---|---|---|
| paciente_id | Title / Rich text | P001 |
| procedimiento | Select | Colecistectomía |
| diagnostico_cie10 | Rich text | K80 |
| medico | Rich text | Dr. Gil |
| urgencia | Select: `programada`, `emergencia` | programada |
| documentos | Multi-select | identificacion, informe_medico, consentimiento, ecografia_abdominal, analitica, presupuesto_hospital |
| costo_estimado | Number | 8000 |
| estado | Status: `pendiente`, `procesado` | pendiente |

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
| decision | Select (3 valores) | Resultado del agente |
| motivo | Rich text | Explicación legible |
| faltantes | Multi-select | Docs a pedir |
| autorizacion_id | Rich text | `AUT-P001-20260926`, solo si aprobada |
| timestamp | Created time | Auditoría |

## 4. Motor de reglas (`evaluar()` en `preauth/reglas.py`)

Orden estricto, el primero que falla corta (fail-fast):

1. **Cobertura**: ¿`procedimiento` está en `cobertura_procedimientos` y no en `exclusiones`? Si no → `DENEGADA`.
2. **Carencia**: `meses_afiliado = (hoy - fecha_inicio) en meses`. Si `urgencia != emergencia` y `antigüedad < carencia_req` → `DENEGADA`. Ej: póliza exige 8 meses, paciente lleva 5 → denegada.
3. **Monto**: si `monto_usado + costo_estimado > monto_maximo` → `DENEGADA`.
4. **Documentos**: `requeridos = DOCS_BASE + DOCS_POR_PROCEDIMIENTO + segunda_opinion si aplica`. `faltantes = requeridos - adjuntos`. Si hay faltantes → `SOLICITUD_DOCUMENTOS_FALTANTES`, si no → `PREAPROBADA`.

Documentos base: `identificacion, informe_medico, consentimiento`.
Por procedimiento: Colecistectomía exige `ecografia_abdominal + analitica`, Artroplastia exige `radiografia + segunda_opinion + analitica`, resto exige `presupuesto_hospital`.

## 5. Cómo ejecutar en local

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

## 6. Ejemplos

| Caso | Entrada | Salida |
|---|---|---|
| Aprobado | P001, Colecistectomía, 32 meses afiliado (póliza desde 01/01/2024, evaluada el 26/09/2026), docs completos, $8000/$50000 | `PREAPROBADA`, `AUT-P001-20260926` |
| Faltan docs | Solo `identificacion + informe_medico` | `SOLICITUD_DOCUMENTOS_FALTANTES`, faltan `consentimiento, ecografia_abdominal, analitica` |
| No cubierto | Rinoplastia estética | `DENEGADA`, no cubierto |
| Carencia | 5 meses afiliado, exige 8 | `DENEGADA`, `No cumple carencia: 5/8 meses` |
| Emergencia | Igual anterior pero `urgencia=emergencia` | Salta carencia, sigue a monto/documentos |

## 7. Rendimiento medido

Medido con `curl` desde Panamá contra el servicio en Render (detalle en [`docs/despliegue.md`](docs/despliegue.md)):

| Escenario | Tiempo |
|---|---|
| Servicio despierto, n = 20 | p50 **0,28 s** · p95 **0,36 s** |
| Motor de reglas (dentro del servidor) | < 0,05 ms por caso |
| Arranque en frío (plan gratuito, tras 15 min sin tráfico) | 32,6 s, mitigado con un keep-alive cada 10 min |

<!-- Agregar p50/p95 de la extracción con IA cuando entre #6. -->

## 8. Datos sintéticos y privacidad

- Todos los pacientes, pólizas e informes del repositorio, de Notion y de la demo son **ficticios**.
- La información de salud es un **dato sensible** según la **Ley 81 de 2019 de protección de datos personales de Panamá** (reglamentada por el Decreto Ejecutivo 285 de 2021). Por eso la demo pública muestra un aviso y no debe recibir datos reales.
- Un uso real requeriría, como mínimo: consentimiento del titular, cifrado en tránsito y en reposo, control de acceso, registro de auditoría y un acuerdo de tratamiento con cada proveedor externo (Notion, OpenAI).
- Los secretos (tokens de Notion y OpenAI) viven solo en variables de entorno de Render, nunca en el repositorio.

## 9. Limitaciones y trabajo futuro
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
