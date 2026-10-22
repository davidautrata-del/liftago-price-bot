"""Jedno měření: vybere trasu, zeptá se Liftaga a zapíše řádek.

Použití:
  python run_once.py                 # náhodná trasa
  python run_once.py --bucket 2      # pásmo 6-10 km (0..4)
"""
import argparse
import logging

import config
from liftago_driver import LiftagoDriver, failure_line
from routes import BUCKETS, pick_route
from sink import write_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("run_once")


def run(bucket=None):
    origin, dest, km = pick_route(bucket)
    log.info("Trasa: %s -> %s (%.1f km)", origin, dest, km)
    if config.PLATFORM == "mac":
        log.info("Pozadí (bez myši a bez fokusu): %s", "ano" if config.BACKGROUND else "ne")
    drv = LiftagoDriver()
    try:
        r = drv.query(origin, dest, km)
        row = [r["ts"], origin, dest, r["km"], r["eta"], r["bez"], r["se"], r["note"]]
    except Exception as e:  # noqa: BLE001
        from datetime import datetime
        row = [datetime.now().strftime("%Y-%m-%d %H:%M"), origin, dest, km, "", "", "",
               f"CHYBA: {type(e).__name__}: {failure_line(e)}"[:300]]
        log.error("Měření selhalo: %s", e)
    finally:
        drv.close()
    write_row(row)
    log.info("Zapsáno: %s", row)
    return row


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bucket", type=int, choices=range(len(BUCKETS)))
    run(ap.parse_args().bucket)
