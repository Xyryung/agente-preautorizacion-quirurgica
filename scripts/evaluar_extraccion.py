"""Mide la precision por campo y la latencia del extractor con los informes sinteticos.

Uso, desde la raiz del repo:
    python -m scripts.evaluar_extraccion --proveedor regex
    python -m scripts.evaluar_extraccion --proveedor openai   (necesita OPENAI_API_KEY en .env)

Imprime tablas en Markdown listas para el README. No forma parte de CI:
con openai hace una llamada real a la API por informe.
"""
import argparse
import json
import statistics
from pathlib import Path

from preauth.config import llm_settings
from preauth.extraccion import extraer
from preauth.texto import norm

DATOS = Path(__file__).resolve().parent.parent / "tests" / "data" / "informes_sinteticos.json"
CAMPOS = ["procedimiento", "urgencia", "costo_estimado", "cie10", "documentos_aportados"]


def coincide(campo: str, obtenido, esperado) -> bool:
    if campo == "costo_estimado":
        if obtenido is None or esperado is None:
            return obtenido is None and esperado is None
        return abs(float(obtenido) - float(esperado)) < 0.01
    if campo == "cie10":
        return (obtenido or "").strip().upper() == (esperado or "").strip().upper()
    if campo == "documentos_aportados":
        return set(obtenido) == set(esperado)
    return obtenido == esperado

def citas_no_encontradas(datos, texto: str) -> list[str]:
    """Citas de 'evidencia' que NO aparecen en el informe (comparando con norm())."""
    t = norm(texto)
    return [e.cita for e in datos.evidencia
            if norm(e.cita.strip(' "\'.…«»“”')) not in t]

def percentil(valores: list[float], p: int) -> float:
    if len(valores) < 2:
        return valores[0]
    return statistics.quantiles(valores, n=100, method="inclusive")[p - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--proveedor", choices=["openai", "regex"], required=True)
    args = parser.parse_args()

    casos = json.loads(DATOS.read_text(encoding="utf-8"))
    aciertos = {c: 0 for c in CAMPOS}
    latencias, respaldos, fallos, filas = [], 0, [], []

    for caso in casos:
        r = extraer(caso["texto"], proveedor=args.proveedor)
        latencias.append(r.latencia_ms)
        estado = "ok" if r.proveedor == args.proveedor else f"RESPALDO ({r.error})"
        print(f"{caso['id']}: {r.latencia_ms:.0f} ms, {estado}", flush=True)
        if r.proveedor != args.proveedor:
            respaldos += 1
            fallos.append(f"{caso['id']}: se uso {r.proveedor} ({r.error})")
        obtenido = r.datos.model_dump()
        errores = 0
        for campo in CAMPOS:
            if coincide(campo, obtenido[campo], caso["esperado"][campo]):
                aciertos[campo] += 1
            else:
                errores += 1
                fallos.append(f"{caso['id']} {campo}: esperado {caso['esperado'][campo]!r}, "
                              f"obtenido {obtenido[campo]!r}")
        no_encontradas = citas_no_encontradas(r.datos, caso["texto"])
        total_citas = len(r.datos.evidencia)
        filas.append((caso["id"], r.datos.confianza, errores,
                      total_citas - len(no_encontradas), total_citas, no_encontradas))

    n = len(casos)
    detalle = ""
    if args.proveedor == "openai":
        cfg = llm_settings()
        detalle = f", modelo `{cfg.openai_model}`, razonamiento `{cfg.openai_reasoning_effort}`"
    print(f"\n### Extraccion con `{args.proveedor}`{detalle} ({n} informes sinteticos)\n")
    print("| Campo | Aciertos | Precision |")
    print("|---|---|---|")
    for campo in CAMPOS:
        print(f"| {campo} | {aciertos[campo]}/{n} | {aciertos[campo] / n:.0%} |")
    print(f"\nLatencia por informe: p50 {percentil(latencias, 50):.0f} ms, "
          f"p95 {percentil(latencias, 95):.0f} ms, max {max(latencias):.0f} ms. "
          f"Respaldos a regex: {respaldos}.")
    print("\n| Informe | Confianza | Campos con error | Citas encontradas en el texto |")
    print("|---|---|---|---|")
    for id_, conf, err, ok, total, _ in filas:
        print(f"| {id_} | {conf:.2f} | {err} | {ok}/{total} |")
    citas_malas = [(id_, c) for id_, *_, malas in filas for c in malas]
    if citas_malas:
        print("\nCitas que no aparecen en el informe:")
        for id_, c in citas_malas:
            print(f"- {id_}: {c!r}")

    if fallos:
        print("\nDiferencias:")
        for f in fallos:
            print(f"- {f}")


if __name__ == "__main__":
    main()
