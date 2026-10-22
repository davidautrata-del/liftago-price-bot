"""Plánovač: každý den vylosuje RUNS_PER_DAY časů (po jednom v každém okně dne)
a ve správný čas spustí měření. Přežije restart (plán je v state/).

  python scheduler.py            # běží trvale (pro launchd)
  python scheduler.py --plan     # jen vypíše dnešní plán
"""
import argparse
import json
import logging
import random
import socket
import subprocess
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("scheduler")
TZ = ZoneInfo(config.TIMEZONE)
GRACE_MIN = 15  # zmeškaný slot starší než tohle se přeskočí (po uspání Macu se nespouští dávka měření)


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def make_plan(day, n=config.RUNS_PER_DAY):
    """n časů rovnoměrně přes den: okno se rozdělí na n stejných dílů a v každém se měření
    umístí do jeho středu s náhodným posunem. Mezi měřeními tak zůstane vždy rozumný odstup."""
    start, end = _hm(config.DAY_START), _hm(config.DAY_END)
    width = (end - start) / n
    jitter = width * 0.25
    slots = []
    for i in range(n):
        center = start + (i + 0.5) * width
        minute = int(round(center + random.uniform(-jitter, jitter)))
        t = datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(minutes=minute)
        slots.append({"time": t.isoformat(timespec="minutes"), "bucket": i % 5, "done": False})
    return slots


def load_plan(day):
    config.STATE_DIR.mkdir(exist_ok=True)
    f = config.STATE_DIR / f"plan-{day.isoformat()}.json"
    if f.exists():
        return f, json.loads(f.read_text())
    plan = make_plan(day)
    f.write_text(json.dumps(plan, indent=1))
    return f, plan


def ensure_appium():
    if config.MOCK:
        return
    host, port = "127.0.0.1", 4723
    with socket.socket() as s:
        s.settimeout(1)
        if s.connect_ex((host, port)) == 0:
            return
    log.info("Startuji Appium server")
    subprocess.Popen(["appium", "--address", host, "--port", str(port)],
                     stdout=open(config.STATE_DIR / "appium.log", "ab"), stderr=subprocess.STDOUT)
    time.sleep(8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args()
    if args.plan:
        _, plan = load_plan(datetime.now(TZ).date())
        for s in plan:
            print(s["time"], "hotovo" if s["done"] else "čeká", "pásmo", s["bucket"])
        return
    import run_once

    while True:
        now = datetime.now(TZ)
        f, plan = load_plan(now.date())
        for s in plan:
            if s["done"]:
                continue
            due = datetime.fromisoformat(s["time"])
            if now < due:
                continue
            s["done"] = True
            f.write_text(json.dumps(plan, indent=1))
            if now - due > timedelta(minutes=GRACE_MIN):
                log.warning("Slot %s zmeškán, přeskakuji", s["time"])
                continue
            try:
                ensure_appium()
                run_once.run(s["bucket"])
            except Exception:  # noqa: BLE001
                log.exception("Běh selhal")
        time.sleep(20)


if __name__ == "__main__":
    main()
