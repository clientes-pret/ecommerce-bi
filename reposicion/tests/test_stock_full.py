"""ml_stock_by_sku: las publicaciones Full sin inventory_id no son stock Full.

Correr:  python3 -m reposicion.tests.test_stock_full
"""
from reposicion import core


def _item(sku, qty, logistic="fulfillment", inventory_id=None, variations=None):
    body = {
        "shipping": {"logistic_type": logistic},
        "available_quantity": qty,
        "seller_custom_field": sku,
        "inventory_id": inventory_id,
    }
    if variations is not None:
        body["variations"] = variations
    return body


def test_full_sin_inventory_id_no_cuenta_como_full():
    # Caso real PRET220-CBONATURAL (2026-11-04): 6 pubs Full sin inventory_id
    # informan 116 (depósito) y el pool real VCPC77336 está en 0.
    details = {f"MLA{i}": _item("SKU1", 116) for i in range(6)}
    details["MLA_REAL"] = _item("SKU1", 0, inventory_id="VCPC77336")
    full, nofull = core.ml_stock_by_sku(details)
    assert full["SKU1"] == 0
    assert nofull.get("SKU1", 0) == 0


def test_full_con_inventory_id_se_deduplica():
    details = {
        "A": _item("SKU2", 40, inventory_id="INV1"),
        "B": _item("SKU2", 40, inventory_id="INV1"),  # mismo pool
        "C": _item("SKU2", 10, inventory_id="INV2"),
    }
    full, _ = core.ml_stock_by_sku(details)
    assert full["SKU2"] == 50


def test_variaciones_full_sin_inventory_id_se_ignoran():
    details = {"V": _item("X", 0, variations=[
        {"seller_custom_field": "SKU3", "available_quantity": 30, "inventory_id": "INV3"},
        {"seller_custom_field": "SKU4", "available_quantity": 99, "inventory_id": None},
    ])}
    full, _ = core.ml_stock_by_sku(details)
    assert full["SKU3"] == 30
    assert full.get("SKU4", 0) == 0


def test_no_full_sigue_contando():
    details = {"N": _item("SKU5", 25, logistic="cross_docking")}
    full, nofull = core.ml_stock_by_sku(details)
    assert nofull["SKU5"] == 25
    assert full.get("SKU5", 0) == 0


if __name__ == "__main__":
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            fn()
            print(f"OK  {nombre}")
