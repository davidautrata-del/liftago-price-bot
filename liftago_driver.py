"""Ovládání Liftago appky přes Appium. Jen zjišťuje cenu, nic neobjednává.

PLATFORM=mac      iPhone appka z App Store běžící na Macu s Apple Silicon (driver mac2)
PLATFORM=android  Android telefon / emulátor (driver uiautomator2)
"""
import json
import logging
import random
import re
import time
from datetime import datetime

import config
from parsing import parse_eta, parse_km, parse_offers, parse_prices

log = logging.getLogger("driver")
_SEL_FILE = "selectors.mac.json" if config.PLATFORM == "mac" else "selectors.json"
SELECTORS = json.loads((config.BASE / _SEL_FILE).read_text())


NO_RESULT = "žádný výsledek"


class PriceNotFound(Exception):
    pass


def _screen_texts(driver):
    src = driver.page_source
    if config.PLATFORM == "mac":
        attrs = r"(?:label|title|value)"
    else:
        attrs = r"(?:text|content-desc)"
    vals = re.findall(attrs + r'="([^"]+)"', src)
    return [v for v in vals if v.strip()]


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _locator(sel):
    from appium.webdriver.common.appiumby import AppiumBy

    by, v = sel["by"], sel["value"]
    if by == "xpath":
        return (AppiumBy.XPATH, v)
    if by == "id":
        return (AppiumBy.ACCESSIBILITY_ID if config.PLATFORM == "mac" else AppiumBy.ID, v)
    if by == "predicate":  # jen mac
        return (AppiumBy.IOS_PREDICATE, v)
    if config.PLATFORM == "mac":
        # text/textcontains: hledá v label, title, value i identifier
        op = "==" if by == "text" else "CONTAINS"
        p = " OR ".join(f"{a} {op} '{_q(v)}'" for a in ("label", "title", "value", "identifier"))
        return (AppiumBy.IOS_PREDICATE, p)
    if by == "text":
        return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{v}")')
    if by == "textcontains":
        return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{v}")')
    if by == "desc":
        return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().descriptionContains("{v}")')
    raise ValueError(f"Neznámý typ lokátoru: {by}")


def _find(driver, sel, timeout=10):
    from selenium.webdriver.support.ui import WebDriverWait

    loc = _locator(sel)
    return WebDriverWait(driver, timeout).until(lambda d: d.find_element(*loc))


class LiftagoDriver:
    def __init__(self):
        if config.MOCK:
            self.driver = None
            return
        from appium import webdriver

        if config.PLATFORM == "mac":
            from appium.options.common import AppiumOptions

            if not config.LIFTAGO_BUNDLE_ID:
                raise RuntimeError("Chybí LIFTAGO_BUNDLE_ID v .env")
            opts = AppiumOptions()
            opts.set_capability("platformName", "mac")
            opts.set_capability("appium:automationName", "mac2")
            opts.set_capability("appium:bundleId", config.LIFTAGO_BUNDLE_ID)
            opts.set_capability("appium:newCommandTimeout", 300)
        else:
            from appium.options.android import UiAutomator2Options

            if not config.LIFTAGO_PACKAGE:
                raise RuntimeError("Chybí LIFTAGO_PACKAGE v .env")
            opts = UiAutomator2Options()
            opts.platform_name = "Android"
            opts.no_reset = True  # zachová přihlášení
            opts.app_package = config.LIFTAGO_PACKAGE
            if config.LIFTAGO_ACTIVITY:
                opts.app_activity = config.LIFTAGO_ACTIVITY
            if config.ANDROID_UDID:
                opts.udid = config.ANDROID_UDID
            opts.new_command_timeout = 300
        self.driver = webdriver.Remote(config.APPIUM_URL, options=opts)

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            finally:
                self._stop_wda()

    @staticmethod
    def _stop_wda():
        """Po skončení relace Appium nechává běžet pomocný proces (xcodebuild / WebDriverAgentMac),
        kvůli kterému macOS pořád hlásí 'Je spuštěná automatizace'. Ukončíme ho."""
        if config.PLATFORM != "mac" or config.MOCK:
            return
        import subprocess

        time.sleep(1)
        for pattern in ("WebDriverAgentMac", "WebDriverAgentRunner"):
            subprocess.run(["pkill", "-f", pattern], check=False)

    def _dump_debug(self, tag="error"):
        config.DEBUG_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            self.driver.save_screenshot(str(config.DEBUG_DIR / f"{stamp}_{tag}.png"))
            (config.DEBUG_DIR / f"{stamp}_{tag}.xml").write_text(self.driver.page_source, encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def _restart_app(self):
        d = self.driver
        if config.PLATFORM == "mac":
            bid = {"bundleId": config.LIFTAGO_BUNDLE_ID}
            try:
                d.execute_script("macos: terminateApp", bid)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.5)
            d.execute_script("macos: activateApp", bid)
        else:
            d.terminate_app(config.LIFTAGO_PACKAGE)
            time.sleep(1)
            d.activate_app(config.LIFTAGO_PACKAGE)
        time.sleep(5)

    def _clear_focused_field(self):
        if config.PLATFORM == "mac":
            # Cmd+A a Delete v aktivním poli (modifierFlags: Command = 16)
            try:
                self.driver.execute_script(
                    "macos: keys", {"keys": [{"key": "a", "modifierFlags": 16}, "\ue017"]})
            except Exception:  # noqa: BLE001
                pass

    def _type_and_pick(self, input_key, text):
        """Do (už otevřeného) pole napíše adresu a vybere první návrh z našeptávače."""
        d = self.driver
        inp = _find(d, SELECTORS[input_key])
        inp.click()
        if config.PLATFORM == "mac":
            self._clear_focused_field()
        else:
            inp.clear()
        inp.send_keys(text)
        time.sleep(2)  # počkat na našeptávač
        _find(d, SELECTORS["first_suggestion"]).click()
        time.sleep(1.5)

    def _tap_optional(self, key, timeout=4):
        """Klikne na prvek, pokud se objeví (např. popup 'Pro koho je tato jízda')."""
        try:
            _find(self.driver, SELECTORS[key], timeout=timeout).click()
            time.sleep(1.5)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _element_texts(self, el):
        names = ("label", "title", "value") if config.PLATFORM == "mac" else ("text", "content-desc")
        out = []
        for e in [el] + el.find_elements("xpath", ".//*"):
            for n in names:
                try:
                    v = e.get_attribute(n)
                except Exception:  # noqa: BLE001
                    v = None
                if v and v.strip():
                    out.append(v)
        return out

    def _category_texts(self):
        """Texty jen z řádku kategorie 'Odvoz vozu' (cena, ETA), ne z celé obrazovky."""
        deadline = time.time() + SELECTORS.get("price_wait_seconds", 30)
        last = []
        while time.time() < deadline:
            try:
                row = _find(self.driver, SELECTORS["category_row"], timeout=3)
                container = row.find_element("xpath", SELECTORS.get("row_container_xpath", ".."))
                last = self._element_texts(container)
                bez, _, _ = parse_prices(last)
                if bez is not None:
                    time.sleep(2)  # nechat doběhnout případnou aktualizaci ceny/ETA
                    container = row.find_element("xpath", SELECTORS.get("row_container_xpath", ".."))
                    return self._element_texts(container)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1.5)
        raise PriceNotFound(f"Cena v řádku Odvoz vozu nenalezena (texty: {last[:6]})")

    def _find_category_row(self):
        """Najde řádek 'Odvoz vozu'; když není vidět, rozbalí další kategorie ('3 další').
        Vrací element, nebo None, když kategorie v seznamu není."""
        d = self.driver
        for _ in range(2):
            try:
                row = _find(d, SELECTORS["category_row"], timeout=6)
                if row.is_displayed():
                    return row
            except Exception:  # noqa: BLE001
                pass
            if not self._tap_optional("more_categories", timeout=3):
                break
        try:
            return _find(d, SELECTORS["category_row"], timeout=4)
        except Exception:  # noqa: BLE001
            return None

    def _collect_offers(self):
        """Počká OFFERS_WAIT s a sesbírá všechny ceny nabídek řidičů (i ty, co mezitím vyprší).

        Nikdy neklikáme na 'Objednat'. Vrací (poznámka, [ceny])."""
        wait = SELECTORS.get("offers_wait_seconds", 20)
        end = time.time() + wait
        seen = {}  # (jméno, cena) -> příjezd; pořadí podle prvního výskytu
        while time.time() < end:
            for name, price, eta in parse_offers(_screen_texts(self.driver)):
                seen.setdefault((name, price), eta)
            time.sleep(2)
        # poslední pohled po uplynutí času
        for name, price, eta in parse_offers(_screen_texts(self.driver)):
            seen.setdefault((name, price), eta)
        prices = [p for (_, p) in seen]
        note = f"{len(prices)} nabídek" if prices else "žádná nabídka"
        return note, prices

    def _close_offers(self):
        """Zavře obrazovku nabídek (tlačítko 'Zavřít') a případně potvrdí zrušení poptávky."""
        try:
            _find(self.driver, SELECTORS["close_offers"], timeout=5).click()
            time.sleep(1.5)
            if "cancel_confirm" in SELECTORS:
                self._tap_optional("cancel_confirm", timeout=3)
        except Exception:  # noqa: BLE001
            log.warning("Nepodařilo se zavřít obrazovku nabídek")

    def query(self, origin, destination, planned_km):
        """Vrátí slovník s výsledky. Při chybě uloží screenshot a vyhodí výjimku.

        Průběh v appce: 'Kam pojedeme?' -> změna adresy vyzvednutí -> cíl jízdy ->
        popup 'Pro koho je tato jízda' (Potvrdit) -> seznam kategorií -> řádek 'Odvoz vozu'.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        if config.MOCK:
            base = 60 + planned_km * 28
            bez = round(base * random.uniform(0.95, 1.1))
            return {"ts": now, "km": planned_km, "eta": random.randint(3, 15),
                    "bez": bez, "se": f"{round(bez * 0.95)}, {round(bez * 1.05)}", "note": "MOCK"}
        d = self.driver
        try:
            self._restart_app()
            _find(d, SELECTORS["open_search"]).click()      # 'Kam pojedeme?'
            time.sleep(1.5)
            _find(d, SELECTORS["pickup_field"]).click()      # adresa vyzvednutí
            time.sleep(1)
            self._type_and_pick("pickup_input", origin)
            self._tap_optional("confirm_pickup")             # 'Potvrdit adresu vyzvednutí' (jen u některých adres)
            self._type_and_pick("destination_input", destination)  # 'Zadejte cíl jízdy'
            self._tap_optional("confirm_dropoff", timeout=5)  # 'Potvrdit cíl jízdy' (oblasti s pevnými body)
            self._tap_optional("confirm_popup")              # 'Potvrdit'
            row = self._find_category_row()
            if row is None:
                return {"ts": now, "km": planned_km, "eta": None, "bez": NO_RESULT,
                        "se": NO_RESULT, "note": "Odvoz vozu v nabídce není"}
            label = row.get_attribute("label") or ""
            if "Pouze později" in label:
                return {"ts": now, "km": planned_km, "eta": None, "bez": NO_RESULT,
                        "se": NO_RESULT, "note": "Odvoz vozu: pouze později"}
            texts = self._category_texts()
            bez, _, note = parse_prices(texts)
            km, eta = parse_km(texts) or planned_km, parse_eta(texts)
            # označit kategorii, přejít na nabídky řidičů a po čtení poptávku zavřít
            row = _find(d, SELECTORS["category_row"])
            if not (row.get_attribute("label") or "").startswith("vybráno"):
                row.click()  # už vybranou kategorii neklikáme (otevřelo by se info o službě)
                time.sleep(1)
            # klikneme jen na přesně 'Přejít k výběru řidiče · Odvoz vozu' (jiná kategorie = nepokračovat)
            try:
                go = _find(d, SELECTORS["go_to_drivers"], timeout=6)
            except Exception:  # noqa: BLE001
                self._dump_debug("go_button_missing")
                return {"ts": now, "km": km, "eta": eta, "bez": bez, "se": NO_RESULT,
                        "note": "tlačítko 'Přejít k výběru řidiče · Odvoz vozu' nenalezeno"}
            go.click()
            offers_note, offers = self._collect_offers()
            self._close_offers()
            note = "; ".join(x for x in (note, offers_note) if x)
            return {
                "ts": now, "km": km, "eta": eta, "bez": bez,
                "se": ", ".join(f"{p:g}" for p in offers), "note": note,
            }
        except Exception:
            config.DEBUG_DIR.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                d.save_screenshot(str(config.DEBUG_DIR / f"{stamp}.png"))
                (config.DEBUG_DIR / f"{stamp}.xml").write_text(d.page_source, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
            raise
