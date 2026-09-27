"""Esquema de Notion como única fuente de verdad.

Todos los nombres de propiedades, estados y decisiones viven aquí.
Ningún otro módulo debe escribir un nombre de propiedad literal:
importa estas constantes en su lugar (convención snake_case).
"""

# --- Informes_Hospital ---
INF_PACIENTE_ID = "paciente_id"
INF_PROCEDIMIENTO = "procedimiento"
INF_DIAGNOSTICO = "diagnostico_cie10"
INF_MEDICO = "medico"
INF_URGENCIA = "urgencia"
INF_DOCUMENTOS = "documentos"
INF_COSTO = "costo_estimado"
INF_ESTADO = "estado"
INF_TEXTO = "informe_texto"

INFORMES_PROPS = {
    INF_PACIENTE_ID: "title",
    INF_PROCEDIMIENTO: "select",
    INF_DIAGNOSTICO: "rich_text",
    INF_MEDICO: "rich_text",
    INF_URGENCIA: "select",
    INF_DOCUMENTOS: "multi_select",
    INF_COSTO: "number",
    INF_ESTADO: "status",
    INF_TEXTO: "rich_text",
}

# --- Pólizas_Aseguradora ---
POL_PACIENTE_ID = "paciente_id"
POL_COBERTURA = "cobertura"
POL_FECHA_INICIO = "fecha_inicio"
POL_CARENCIA = "carencia"
POL_MONTO_MAX = "monto_maximo"
POL_MONTO_USADO = "monto_usado"
POL_EXCLUSIONES = "exclusiones"
POL_SEGUNDA_OPINION = "requiere_segunda_opinion"

POLIZAS_PROPS = {
    POL_PACIENTE_ID: "title",
    POL_COBERTURA: "multi_select",
    POL_FECHA_INICIO: "date",
    POL_CARENCIA: "rich_text",
    POL_MONTO_MAX: "number",
    POL_MONTO_USADO: "number",
    POL_EXCLUSIONES: "multi_select",
    POL_SEGUNDA_OPINION: "multi_select",
}

# --- Resoluciones ---
RES_PACIENTE_ID = "paciente_id"
RES_DECISION = "decision"
RES_MOTIVO = "motivo"
RES_FALTANTES = "faltantes"
RES_AUTORIZACION = "autorizacion_id"
RES_TIMESTAMP = "timestamp"
RES_INFORME = "informe"

RESOLUCIONES_PROPS = {
    RES_PACIENTE_ID: "title",
    RES_DECISION: "select",
    RES_MOTIVO: "rich_text",
    RES_FALTANTES: "multi_select",
    RES_AUTORIZACION: "rich_text",
    RES_TIMESTAMP: "created_time",
    RES_INFORME: "relation",
}

# --- Valores de estado / decisión (únicos permitidos) ---
ESTADO_PENDIENTE = "pendiente"
ESTADO_PROCESADO = "procesado"

URG_EMERGENCIA = "emergencia"
URG_PROGRAMADA = "programada"
