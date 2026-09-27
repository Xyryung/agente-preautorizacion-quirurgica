"""Issue #15: reservar el monto usado tras una pre-aprobación."""
from datetime import date

from preauth.reglas import (
    Decision,
    InformeMedico,
    Poliza,
    evaluar,
    reservar,
)

DOCS = [
    "identificacion", "informe_medico", "consentimiento",
    "ecografia_abdominal", "analitica", "presupuesto_hospital",
]
HOY = date(2026, 9, 26)


def poliza(**kw):
    base = dict(paciente_id="P001", cobertura_procedimientos=["Colecistectomía"],
                fecha_inicio=date(2024, 1, 1), carencia_meses={"default": 8},
                monto_maximo=10000, monto_usado=5000)
    base.update(kw)
    return Poliza(**base)


def informe(costo=3000):
    return InformeMedico("P001", "Colecistectomía", "K80", "Dr. Gil",
                         documentos_adjuntos=list(DOCS), costo_estimado=costo)


def test_reservado_cuenta_en_regla_de_monto():
    # usado 5000 + reservado 3000 + estimado 3000 > 10000 -> denegada
    p = poliza(monto_reservado=3000)
    r = evaluar(p, informe(3000), HOY)
    assert r["decision"] == Decision.DENEGADA


def test_sin_reservado_pasa():
    p = poliza()
    r = evaluar(p, informe(3000), HOY)
    assert r["decision"] == Decision.PREAPROBADA
    assert r["monto_reservado"] == 3000


def test_dos_preaprobaciones_seguidas_superan_maximo():
    p = poliza()
    r1 = evaluar(p, informe(3000), HOY)
    assert r1["decision"] == Decision.PREAPROBADA
    p2 = reservar(p, 3000)  # la primera reserva ya cuenta
    r2 = evaluar(p2, informe(3000), HOY)
    assert r2["decision"] == Decision.DENEGADA


def test_reservar_no_mutay_acumula():
    p = poliza()
    p2 = reservar(p, 1000)
    assert p.monto_reservado == 0
    assert p2.monto_reservado == 1000
    assert reservar(p2, 500).monto_reservado == 1500
