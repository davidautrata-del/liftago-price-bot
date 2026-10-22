"""Parsování textů z obrazovky Liftaga (ceny, ETA, vzdálenost). Bez závislosti na Appiu."""
import re

_PRICE = re.compile(r"(\d[\d\s  ]*(?:[.,]\d+)?)\s*(?:Kč|CZK)", re.I)
_RANGE = re.compile(r"(\d[\d\s  ]*)\s*[–\-]\s*(\d[\d\s  ]*)\s*(?:Kč|CZK)", re.I)
_ETA = re.compile(r"(\d+)\s*min", re.I)
_KM = re.compile(r"(\d+(?:[.,]\d+)?)\s*km", re.I)


def _num(s):
    s = re.sub(r"[\s  ]", "", s).replace(",", ".")
    return float(s)


def parse_prices(texts):
    """Vrátí (cena_bez_slevy, cena_se_slevou, poznámka).

    Heuristika: Liftago u slevy ukazuje dvě ceny (přeškrtnutou původní a novou).
    Vyšší = bez slevy, nižší = se slevou. Jedna cena = bez slevy i se slevou stejná.
    Rozsah „250–300 Kč“ se uloží jako střed a zapíše se do poznámky.
    """
    note = ""
    prices = []
    for t in texts:
        m = _RANGE.search(t)
        if m:
            lo, hi = _num(m.group(1)), _num(m.group(2))
            prices.append((lo + hi) / 2)
            note = f"rozsah {lo:g}-{hi:g} Kč"
            continue
        for m in _PRICE.finditer(t):
            prices.append(_num(m.group(1)))
    prices = sorted(set(prices))
    if not prices:
        return None, None, note
    if len(prices) == 1:
        return prices[0], prices[0], note
    if len(prices) > 2:
        note = (note + "; " if note else "") + f"na obrazovce {len(prices)} cen: {prices}"
    return max(prices), min(prices), note


def parse_offers(texts):
    """Nabídky řidičů z obrazovky 'Vyberte si řidiče'.

    Texty jdou za sebou v pořadí na obrazovce: jméno, 'Příjezd', '14 min', 'Fixní cena', '545 Kč'.
    Vrací seznam (jméno, cena, příjezd_min). Neznámé jméno = ''.
    """
    offers = []
    # prvek může dát label i value se stejným textem; sloučit sousední duplicity
    texts = [t for k, t in enumerate(texts) if k == 0 or t != texts[k - 1]]
    for i, t in enumerate(texts):
        m = re.fullmatch(r"\s*(\d[\d\s  ]*(?:[.,]\d+)?)\s*(?:Kč|CZK)\s*", t)
        if not m:
            continue
        name, eta = "", None  # najdi nejbližší 'Příjezd' před cenou
        for j in range(i - 1, max(i - 6, -1), -1):
            if texts[j].strip().lower().startswith("příjezd"):
                name = texts[j - 1] if j >= 1 else ""
                if j + 1 < len(texts):
                    em = _ETA.search(texts[j + 1])
                    eta = int(em.group(1)) if em else None
                break
        offers.append((name, _num(m.group(1)), eta))
    return offers


def parse_eta(texts):
    for t in texts:
        m = _ETA.search(t)
        if m:
            return int(m.group(1))
    return None


def parse_km(texts):
    for t in texts:
        m = _KM.search(t)
        if m:
            return _num(m.group(1))
    return None
