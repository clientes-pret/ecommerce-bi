#!/usr/bin/env python3
"""
reposicion/jobs/backfill_ventas.py — carga histórica inicial del dataset de
ventas persistente (repo_ventas_items). Correr una sola vez por canal (o de
nuevo si se quiere extender el histórico hacia atrás) — no es un cron, es
manual.

Uso:
    python3 -m reposicion.jobs.backfill_ventas --dias 365 --canal todos
    python3 -m reposicion.jobs.backfill_ventas --dias 90 --canal ml_pret
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reposicion import core, db, ventas_sync

CANALES = ["tn_pret", "tn_lavan", "ml_pret", "ml_lavan"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dias", type=int, default=365)
    parser.add_argument("--canal", choices=CANALES + ["todos"], default="todos")
    args = parser.parse_args()

    config = db.load_config()
    canales = CANALES if args.canal == "todos" else [args.canal]

    for canal in canales:
        cfg = config["channels"].get(canal)
        if not cfg or not cfg.get("enabled", True):
            core.tnlog(f"  (canal {canal} deshabilitado, se omite)")
            continue

        item_details = None
        if canal.startswith("ml_"):
            core.tnlog(f"→ {cfg['label']}: bajando catálogo ML para resolver SKUs de Full...")
            token = core.ml_ensure_token(canal, cfg)
            item_ids = core.ml_scroll_item_ids(token, cfg["user_id"])
            item_details = core.ml_items_by_ids(token, item_ids)

        ventas_sync.backfill(config, canal, days=args.dias, item_details=item_details)


if __name__ == "__main__":
    main()
