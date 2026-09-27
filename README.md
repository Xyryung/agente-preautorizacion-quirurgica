# Agente de Pre-Autorización Quirúrgica en Tiempo Real

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
| decision | Select (3 valores) | Resultado del agente |
| motivo | Rich text | Explicación legible |
| faltantes | Multi-select | Docs a pedir |
| autorizacion_id | Rich text | `AUT-P001-20260926`, solo si aprobada |
| timestamp | Created time | Auditoría |

## 4. Motor de reglas (`evaluar()` en `preauth/reglas.py:47`)

Orden estricto, el primero que falla corta (fail-fast):

1. **Cobertura** (`:51-54`): ¿`procedimiento` está en `cobertura_procedimientos` y no en `exclusiones`? Si no → `DENEGADA`.
2. **Carencia** (`:57-63`): `meses_afiliado = (hoy - fecha_inicio) en meses`. Si `urgencia != emergencia` y `antigüedad < carencia_req` → `DENEGADA`. Ej: póliza exige 8 meses, paciente lleva 5 → denegada.
3. **Monto** (`:66-67`): si `monto_usado + costo_estimado > monto_maximo` → `DENEGADA`.
4. **Documentos** (`:70-78`): `requeridos = DOCS_BASE + DOCS_POR_PROCEDIMIENTO + segunda_opinion si aplica`. `faltantes = requeridos - adjuntos`. Si hay faltantes → `SOLICITUD_DOCUMENTOS_FALTANTES`, si no → `PREAPROBADA`.

Documentos base (`:37`): `identificacion, informe_medico, consentimiento`.
Por procedimiento (`:38-42`): Colecistectomía exige `ecografia_abdominal + analitica`, Artroplastia exige `radiografia + segunda_opinion + analitica`, resto exige `presupuesto_hospital`.

## 5. Instalación y uso

```bash
# 1. Requisitos
pip install notion-client

# 2. Variables de entorno
export NOTION_TOKEN="secret_xxx"
export NOTION_DB_INFORMES="id_db_informes"
export NOTION_DB_POLIZAS="id_db_polizas"
export NOTION_DB_RESOLUCIONES="id_db_resoluciones"

# 3. Prueba local (sin Notion)
python -m preauth.reglas
# Salida esperada: Caso 1 PREAPROBADA, Caso 2 FALTANTES, Caso 3 DENEGADA
```

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
| Aprobado | P001, Colecistectomía, 20 meses afiliado, docs completos, $8000/$50000 | `PREAPROBADA`, `AUT-P001-20260926` |
| Faltan docs | Solo `identificacion + informe_medico` | `SOLICITUD_DOCUMENTOS_FALTANTES`, faltan `consentimiento, ecografia_abdominal, analitica` |
| No cubierto | Rinoplastia estética | `DENEGADA`, no cubierto |
| Carencia | 5 meses afiliado, exige 8 | `DENEGADA`, `No cumple carencia: 5/8 meses` |
| Emergencia | Igual anterior pero `urgencia=emergencia` | Salta carencia, sigue a monto/documentos |

## 7. Limitaciones y trabajo futuro
- Matching de `procedimiento` es por string exacto → normalizar a códigos CPT/CIE o usar embeddings/LLM para informe libre.
- Sin OCR/PDF: hoy `documentos` es checklist; integrar OCR para verificar contenido real.
- Sin autenticación, auditoría HIPAA/GDPR ni reintentos: añadir log inmutable, cifrado y cola con retries.
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
| ... | ... | ... |
