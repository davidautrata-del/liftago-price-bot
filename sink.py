"""Zápis výsledků: vždy do lokálního CSV, a když je to nastavené, i do Google Sheetu.
Při výpadku sheetu se řádky uloží do fronty a odešlou při dalším běhu."""
import csv
import json
import logging
import os

import config

log = logging.getLogger("sink")

HEADER = [
    "Date & time", "From", "To", "Distance (km)", "ETA (min)",
    "Price without discount (CZK)", "Driver offers (CZK)", "Note",
]
PENDING = config.STATE_DIR / "pending_rows.jsonl"


def _write_csv(row):
    new = not config.CSV_FILE.exists()
    with open(config.CSV_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        w.writerow(row)


def _worksheet():
    import gspread  # import až tady, ať mock režim funguje bez něj

    creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS není nastavené")
    gc = gspread.service_account(filename=creds)
    return gc.open_by_key(config.SHEET_ID).worksheet(config.SHEET_TAB)


def _load_pending():
    if not PENDING.exists():
        return []
    return [json.loads(line) for line in PENDING.read_text().splitlines() if line.strip()]


def _push_webhook(rows):
    import requests

    r = requests.post(os.environ["SHEET_WEBHOOK_URL"], json={"rows": rows}, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        snippet = " ".join(r.text.split())[:150]
        raise RuntimeError(f"webhook nevrátil JSON (HTTP {r.status_code}, {r.headers.get('content-type')}): {snippet}")
    if not data.get("ok"):
        raise RuntimeError(data.get("error", "webhook odmítl data"))


def write_row(row):
    """row = seznam hodnot ve stejném pořadí jako HEADER.

    Vždy CSV. Do sheetu přes Apps Script webhook (SHEET_WEBHOOK_URL), jinak přes service account."""
    config.STATE_DIR.mkdir(exist_ok=True)
    _write_csv(row)
    rows = _load_pending() + [row]
    webhook = bool(os.getenv("SHEET_WEBHOOK_URL"))
    service_acc = os.path.isfile(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""))
    if not (webhook or service_acc):
        PENDING.write_text("")  # sheet není nastavený, CSV stačí
        log.info("Sheet nenastaven, zapsáno jen do CSV")
        return
    try:
        if webhook:
            _push_webhook(rows)
        else:
            _worksheet().append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
        PENDING.write_text("")
    except Exception as e:  # noqa: BLE001
        msg = str(e).replace(os.getenv("SHEET_WEBHOOK_URL", "\0"), "<webhook-url>")
        log.warning("Zápis do sheetu selhal (%s: %s), řádek ve frontě", type(e).__name__, msg[:300])
        PENDING.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
