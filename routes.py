"""Výběr tras 1-20 km. Délka se počítá přes OSRM (silniční), záloha haversine * 1.3."""
import itertools
import json
import math
import random
from functools import lru_cache
from pathlib import Path

import requests

from addresses import ADDRESSES

CACHE_FILE = Path(__file__).parent / "state" / "route_cache.json"
# Cílová pásma délky (km). Střídají se, aby data pokryla celý rozsah 1-20 km.
BUCKETS = [(1, 3), (3, 6), (6, 10), (10, 15), (15, 20)]


def haversine_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))


def road_km(a, b):
    """Silniční vzdálenost v km mezi (lat, lon) body."""
    key = f"{a[0]:.4f},{a[1]:.4f}|{b[0]:.4f},{b[1]:.4f}"
    cache = _load_cache()
    if key in cache:
        return cache[key]
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{a[1]},{a[0]};{b[1]},{b[0]}?overview=false"
        )
        r = requests.get(url, timeout=8)
        km = round(r.json()["routes"][0]["distance"] / 1000, 1)
    except Exception:
        km = round(haversine_km(a, b) * 1.3, 1)
    cache[key] = km
    _save_cache(cache)
    return km


def pick_route(bucket_index=None, rng=random):
    """Vrátí (odkud, kam, km) s délkou v zadaném pásmu. Bez indexu pásmo náhodně."""
    if bucket_index is None:
        bucket_index = rng.randrange(len(BUCKETS))
    lo, hi = BUCKETS[bucket_index % len(BUCKETS)]
    pairs = list(itertools.permutations(ADDRESSES, 2))
    rng.shuffle(pairs)
    # předfiltr levným haversine, ať nevoláme OSRM zbytečně
    for a, b in pairs:
        approx = haversine_km(a[1:], b[1:]) * 1.3
        if not (lo * 0.7 <= approx <= hi * 1.3):
            continue
        km = road_km(a[1:], b[1:])
        if lo <= km < hi:
            return a[0], b[0], km
    # nic nenalezeno: vezmi libovolnou dvojici do 20 km
    for a, b in pairs:
        km = road_km(a[1:], b[1:])
        if 1 <= km <= 20:
            return a[0], b[0], km
    raise RuntimeError("Nepodařilo se vybrat trasu")
