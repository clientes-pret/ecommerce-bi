"""Pruebas de reposicion/ml_control_sync.py (lógica pura, sin red ni base de datos).

Correr:  python3 -m reposicion.tests.test_ml_control
"""

from datetime import datetime, timezone

from reposicion.ml_control_sync import (falta_detalle, fila_envio, fila_reclamo, filas_alertas, resumen_reputacion,
                                        riesgo_medidas, semana_envios, texto_motivo)

AHORA = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)       # jueves 26/11, 12:00 en Argentina


def test_reputacion_protegida_guarda_el_valor_real():
    r = resumen_reputacion({"level_id": "5_green", "metrics": {
        "claims": {"value": 0, "rate": 0, "excluded": {"real_value": 28, "real_rate": 0.0124}},
        "delayed_handling_time": {"value": 3, "rate": 0.001}}})
    assert r["protegida"] and r["reclamos_60d"] == 28 and r["reclamos_tasa"] == 1.24
    assert r["demoras_60d"] == 3 and r["demoras_tasa"] == 0.1 and r["cancel_tasa"] is None


def reclamo(estado="opened", acciones=(), **extra):
    return {"id": 1, "type": "mediations", "stage": "claim", "status": estado, "resource": "order", "resource_id": 99,
            "reason_id": "PDD9939", "date_created": "2026-11-20T10:00:00.000-04:00", "last_updated": "2026-11-21T10:00:00.000-04:00",
            "players": [{"role": "complainant", "available_actions": [{"action": "x", "mandatory": True, "due_date": "2026-11-01T00:00:00-04:00"}]},
                        {"role": "respondent", "available_actions": list(acciones)}], **extra}


def test_vence_es_la_obligatoria_nuestra_mas_proxima():
    f = fila_reclamo(reclamo(acciones=[
        {"action": "send_message", "mandatory": True, "due_date": "2026-11-30T22:00:00.000-04:00"},
        {"action": "refund", "mandatory": False, "due_date": "2026-11-26T10:00:00.000-04:00"},
        {"action": "mediador", "mandatory": True, "due_date": "2026-11-27T13:00:00.000-04:00"}]), "pret")
    assert f["vence"] == "2026-11-27T13:00:00.000-04:00" and f["accion"] == "mediador" and f["order_id"] == 99


def test_cerrado_no_tiene_vencimiento():
    f = fila_reclamo(reclamo("closed", [{"action": "a", "mandatory": True, "due_date": "2026-11-30T22:00:00-04:00"}]), "pret")
    assert f["vence"] is None


def test_detalle_solo_si_cambio():
    f = fila_reclamo(reclamo(), "pret")
    igual = {"afecta": "affected", "actualizado": "2026-11-21T14:00:00+00:00", "estado": "opened"}   # mismo momento, otro huso
    assert falta_detalle(f, None) and not falta_detalle(f, igual)
    assert falta_detalle(f, {**igual, "estado": "closed"}) and falta_detalle(f, {**igual, "afecta": None})
    assert not falta_detalle({**f, "tipo": "cancel_purchase"}, None)


def test_filas_de_reclamo_con_las_mismas_columnas():
    # un reclamo que ML ya no devuelve se guarda cerrado con las mismas columnas que el resto (upsert en lote)
    vacia = fila_reclamo({"id": 5}, "pret")
    assert vacia.keys() == fila_reclamo(reclamo(), "pret").keys() and vacia["estado"] is None


def test_motivos():
    assert texto_motivo("PDD9939", {"name": "repentant_buyer"}) == "No lo quiere (llegó bien)"
    assert texto_motivo("PNR1", {"name": "otro", "detail": "x"}) == "No recibió el producto"
    assert texto_motivo("PDD1", None) == "PDD1"


def envio(logistica, limite, sla, estado="delivered"):
    return fila_envio("pret", 1, {"logistic_type": logistica, "status": estado},
                      {"expected_date": limite, "status": sla} if sla else None, None, {})


def test_semana_cuenta_desde_el_lunes_y_solo_lo_vencido():
    filas = [envio("self_service", "2026-11-23T23:00:00-03:00", "on_time"),            # lunes
             envio("self_service", "2026-11-22T23:00:00-03:00", "delayed"),            # domingo anterior: no
             envio("self_service", "2026-11-25T23:00:00-03:00", "delayed", "cancelled"),  # demorado y cancelado: cuenta
             envio("self_service", "2026-11-25T23:00:00-03:00", "on_time", "cancelled"),  # cancelado a tiempo: no
             envio("self_service", "2026-11-26T23:00:00-03:00", "on_time", "ready_to_ship"),  # todavía no vence
             envio("self_service", None, None, "cancelled"),
             envio("cross_docking", "2026-11-24T15:00:00-03:00", "early")]
    assert semana_envios(filas, "self_service", AHORA) == (2, 1)
    assert semana_envios(filas, "cross_docking", AHORA) == (1, 0)


def test_full_queda_terminado():
    assert envio("fulfillment", None, None, "ready_to_ship")["final"]
    assert not envio("self_service", None, None, "shipped")["final"]


def publicacion(declarado, otro, fuente, logistica="cross_docking", estado="active"):
    def attrs(pref, p):
        return [{"id": pref + k, "value_name": v} for k, v in zip(("HEIGHT", "WIDTH", "LENGTH", "WEIGHT"), p)]
    return {"id": "MLA1", "title": "X", "status": estado, "shipping": {"logistic_type": logistica},
            "attributes": attrs("SELLER_PACKAGE_", declarado) + attrs("PACKAGE_", otro) +
            [{"id": "PACKAGE_DATA_SOURCE", "value_name": fuente}]}


def test_riesgo_ml_midio_mas():
    r = riesgo_medidas(publicacion(("10 cm", "10 cm", "10 cm", "500 g"), ("10 cm", "10 cm", "10 cm", "0.7 kg"), "MEASUREMENT"))
    assert r["tipo"] == "ml_midio_mas" and r["dif_peso"] == 1.4 and r["otro"] == "10 × 10 × 10 cm · 700 g"
    assert riesgo_medidas(publicacion(("10 cm", "10 cm", "10 cm", "500 g"), ("10 cm", "10 cm", "10 cm", "600 g"), "MEASUREMENT")) is None


def test_riesgo_datos_distintos_y_full_afuera():
    d, o = ("43 cm", "14 cm", "25 cm", "1200 g"), ("15 cm", "6 cm", "10 cm", "1200 g")
    assert riesgo_medidas(publicacion(d, o, "SELLER"))["tipo"] == "datos_distintos"
    assert riesgo_medidas(publicacion(d, o, "SELLER", "fulfillment")) is None
    assert riesgo_medidas(publicacion(d, o, "SELLER", estado="paused")) is None


def test_pendiente_solo_la_ultima_alerta_de_una_publicacion_frenada():
    inf = [{"id": "1", "related_item_id": "MLA1", "date_created": "2026-11-01T10:00:00-0400", "reason": "<p>Medidas mayores.</p>"},
           {"id": "2", "related_item_id": "MLA1", "date_created": "2026-11-26T08:00:00-0400", "reason": "<p>Cometiste una nueva irregularidad</p>"}]
    item = {"id": "MLA1", "title": "X", "status": "under_review", "sub_status": ["waiting_for_patch"], "attributes": []}
    f = {x["id"]: x for x in filas_alertas("lavan", inf, [item])}
    assert f["2"]["pendiente"] and not f["1"]["pendiente"] and f["2"]["reincidencia"] and f["1"]["motivo"] == "Medidas mayores."
    assert not filas_alertas("lavan", inf, [{**item, "status": "active", "sub_status": []}])[1]["pendiente"]


if __name__ == "__main__":
    pruebas = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for nombre, f in pruebas:
        f()
        print("ok", nombre)
    print(f"{len(pruebas)} pruebas bien")
