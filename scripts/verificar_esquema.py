"""Verifica que el esquema real de Notion coincida con preauth/esquema.py.

Uso: python scripts/verificar_esquema.py
Termina sin diferencias (exit 0) si todo coincide; exit 1 con reporte si no.
Sin credenciales (NOTION_TOKEN / DB ids) valida solo coherencia interna:
que ningún nombre de propiedad aparezca literal fuera de esquema.py.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from preauth import esquema as E

ESPERADO = {
    "informes": E.INFORMES_PROPS,
    "polizas": E.POLIZAS_PROPS,
    "resoluciones": E.RESOLUCIONES_PROPS,
}

# 1. Chequeo local: accesos a propiedades Notion fuera de esquema.py
# Solo cuentan accesos reales a Notion: get_text(pr, "..."), filter {"property": "..."}
# y claves con espacio (snake_case obligatorio). Los dicts internos de evaluar()
# o los docstrings no cuentan.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAS = set()
for d in ESPERADO.values():
    TODAS.update(d.keys())
PAT = re.compile(r'get_text\(\s*pr\s*,\s*"([^"]+)"\)|"property"\s*:\s*"([^"]+)"')
ESPACIO_PAT = re.compile(r'"(costo estimado|monto maximo|monto usado|carencia )"')
ERRORES = []
# get2 / nombres alternativos prohibidos (solo definiciones/llamadas reales, no menciones en texto)
ALT_PAT = re.compile(r'^\s*(def\s+get2|.*=\s*get2|get2\(pr)')
SKIP = ("preauth/esquema.py", "verificar_esquema.py", "create_issues.py")
for dirpath, _, files in os.walk(RAIZ):
    if ".git" in dirpath:
        continue
    for f in files:
        if not f.endswith(".py"):
            continue
        p = os.path.join(dirpath, f)
        if any(p.endswith(s) for s in SKIP):
            continue
        src = open(p, encoding="utf-8").read()
        for line in src.splitlines():
            if ALT_PAT.search(line):
                ERRORES.append(f"{p}: usa get2() (nombres alternativos prohibidos)")
        for m in ESPACIO_PAT.finditer(src):
            ERRORES.append(f"{p}: nombre con espacio {m.group(1)!r} prohibido (usa snake_case de esquema.py)")
        for m in PAT.finditer(src):
            lit = m.group(1) or m.group(2)
            if lit not in TODAS:
                ERRORES.append(f"{p}: propiedad {lit!r} no está en esquema.py")

if ERRORES:
    print("DIFERENCIAS (uso de literales / get2):")
    for e in ERRORES:
        print(" -", e)
    sys.exit(1)

# 2. Chequeo remoto (solo si hay credenciales)
token = os.environ.get("NOTION_TOKEN")
dbs = {"informes": os.environ.get("NOTION_DB_INFORMES"),
       "polizas": os.environ.get("NOTION_DB_POLIZAS"),
       "resoluciones": os.environ.get("NOTION_DB_RESOLUCIONES")}
if not token or not all(dbs.values()):
    print("OK (coherencia local): sin credenciales, no se consultó Notion.")
    sys.exit(0)

from notion_client import Client
notion = Client(auth=token)


def props_reales(db):
    try:
        return notion.data_sources.retrieve(data_source_id=db)["properties"]
    except Exception:
        info = notion.databases.retrieve(database_id=db)
        ds = info.get("data_sources", [])
        if not ds:
            raise
        return notion.data_sources.retrieve(data_source_id=ds[0]["id"])["properties"]


fallos = []
for nombre, esperado in ESPERADO.items():
    reales = props_reales(dbs[nombre])
    for prop, tipo in esperado.items():
        if prop not in reales:
            fallos.append(f"{nombre}: falta propiedad {prop!r} (esperado {tipo})")
        elif reales[prop].get("type") != tipo:
            fallos.append(f"{nombre}.{prop}: tipo {reales[prop].get('type')!r} != {tipo!r}")
if fallos:
    print("DIFERENCIAS con Notion:")
    for f in fallos:
        print(" -", f)
    sys.exit(1)
print("OK: esquema de Notion coincide con preauth/esquema.py")
