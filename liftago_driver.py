"""Ovládání Liftago appky přes Appium. Jen zjišťuje cenu, nic neobjednává.

Na Macu při BACKGROUND=1 (výchozí) běží měření na pozadí: okno se schová,
tlačítka jdou přes AXPress. Kurzor se neposouvá a fokus zůstává uživateli.

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
_GO_PREFIX = "Přejít k výběru řidiče"


class PriceNotFound(Exception):
    pass


def is_order_button(label):
    """Tlačítko, které by jízdu závazně objednalo. To se nesmí stisknout."""
    text = (label or "").strip()
    return text == "Objednat" or text.startswith("Objednat ")


def go_label_suffix(label):
    """None, když to není tlačítko na řidiče. Prázdný řetězec, když v popisku kategorie není."""
    text = (label or "").replace("\u00a0", " ").strip()
    if is_order_button(text) or not text.startswith(_GO_PREFIX):
        return None
    rest = text[len(_GO_PREFIX):].strip()
    return rest.lstrip("·•|–—- ").strip()


def go_label_targets_odvoz(label):
    """Tlačítko vede na řidiče. Holý popisek ještě neříká, která kategorie je vybraná.

    Starší podoba připojovala kategorii za „·“; jiná kategorie se nepřijme.
    """
    rest = go_label_suffix(label)
    if rest is None:
        return False
    if not rest:
        return True
    return rest == "Odvoz vozu" or rest.startswith("Odvoz vozu ")


def go_label_names_odvoz(label):
    """Samotný popisek jmenuje Odvoz vozu. Holé „Přejít k výběru řidiče“ ne."""
    rest = go_label_suffix(label)
    return bool(rest) and (rest == "Odvoz vozu" or rest.startswith("Odvoz vozu "))


def go_label_names_other_category(label):
    rest = go_label_suffix(label)
    return bool(rest) and not go_label_names_odvoz(label)


def suggestion_target(names, value, sel):
    """Jak stisknout návrh adresy. Bez popisku stačí pořadí za značkou, nebo hodnota."""
    texts = []
    for name in names or []:
        cleaned = (name or "").strip()
        if cleaned and cleaned not in texts:
            texts.append(cleaned)
    value = (value or "").strip()
    if any(is_order_button(text) for text in texts) or is_order_button(value):
        raise RuntimeError("Bot nesmí stisknout Objednat")
    if texts:
        return ("label", texts[0])
    marker = ((sel or {}).get("ax_after") or "").strip()
    role = ((sel or {}).get("ax_role") or "AXButton").strip() or "AXButton"
    try:
        nth = int((sel or {}).get("ax_nth") or 1)
    except (TypeError, ValueError):
        nth = 1
    if marker:
        return ("after", marker, role, nth, value)
    if value:
        return ("value", value)
    raise RuntimeError("Prvek nemá popisek, v pozadí ho nelze stisknout")


def label_is_selected_odvoz(label):
    text = (label or "").replace("\u00a0", " ").strip().lower()
    return text.startswith("vybráno") and "odvoz vozu" in text


def _unselected_odvoz_row(labels):
    return any(
        "Odvoz vozu" in label and "Řidič" in label and not label_is_selected_odvoz(label)
        for label in labels
    )


def assess_category_screen(labels):
    """Co udělat na obrazovce kategorií: press, select, wait, later, wrong_category, absent.

    „Přejít k výběru řidiče“ se smí stisknout až když je vybraná Odvoz vozu.
    Potvrzení je popisek začínající „vybráno“ a obsahující Odvoz vozu, nebo starý
    popisek tlačítka s „· Odvoz vozu“. Samotná viditelnost tlačítka nestačí:
    patří kategorii, která je vybraná teď.
    """
    confirmed = any(label_is_selected_odvoz(label) for label in labels)
    explicit = any(go_label_names_odvoz(label) for label in labels)
    bare = any(go_label_suffix(label) == "" for label in labels)
    other = any(go_label_names_other_category(label) for label in labels)
    row = _unselected_odvoz_row(labels)
    later = any("Odvoz vozu" in label and "Pouze později" in label for label in labels)
    if explicit or (confirmed and bare):
        return "press"
    if other and not row:
        return "wrong_category"
    if later and not row:
        return "later"
    if row and not confirmed:
        return "select"
    if confirmed:
        return "wait"
    if other:
        return "wrong_category"
    if later:
        return "later"
    return "absent"


def selection_step(action, presses):
    """Nejdřív jeden klik na řádek. Tlačítko řidičů až po potvrzeném výběru."""
    if action == "press":
        return "done"
    if action in ("wrong_category", "later", "absent"):
        return "stop"
    if action == "select" and presses < 1:
        return "press_row"
    return "wait"


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


def _exact_label(sel):
    """Popisek z predikátu `label == '...'`. Jinak prázdný řetězec."""
    match = re.search(r"label == '([^']*)'", (sel or {}).get("value") or "")
    return match.group(1) if match else ""


def failure_line(exc):
    """Jedna čitelná věta z výjimky. Prázdné 'Message:' ze Selenium se přeskočí."""
    lines = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        lines.extend(str(current).splitlines())
        current = current.__cause__
    cleaned = []
    for raw in lines:
        line = raw.strip()
        if not line or line in {"Message:", "Stacktrace:", "Screenshot: available via screen"}:
            continue
        if line.startswith("at "):
            continue
        if line.startswith("Message:"):
            rest = line[len("Message:"):].strip()
            if rest:
                cleaned.append(rest)
            continue
        cleaned.append(line)
    for line in cleaned:
        lowered = line.lower()
        if "404" in line or "proxyrequesterror" in lowered or "no such element" in lowered or "crashed" in lowered:
            return line
    if cleaned:
        return cleaned[0]
    return type(exc).__name__


def _find(driver, sel, timeout=10):
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    loc = _locator(sel)
    last = None

    def attempt(d):
        nonlocal last
        try:
            return d.find_element(*loc)
        except Exception as exc:  # noqa: BLE001
            last = exc
            raise

    try:
        return WebDriverWait(driver, timeout).until(attempt)
    except TimeoutException as exc:
        detail = failure_line(last or exc)
        raise TimeoutException(detail) from exc


class LiftagoDriver:
    def __init__(self):
        self.driver = None
        self._user_front = None
        self._saved_window = None
        self._offscreen = False
        self._intro_done = False
        if config.MOCK:
            return
        if self._wants_background():
            import mac_background

            self._user_front = mac_background.frontmost_name()
            self._saved_window = mac_background.window_position(config.LIFTAGO_BUNDLE_ID)
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
            if config.BACKGROUND:
                # Konec relace by Liftago jinak ukončil a okno by nešlo vrátit.
                opts.set_capability("appium:skipAppKill", True)
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
        if self._background_mode():
            # Relace mac2 appku aktivuje. Hned ji odsunout a vrátit fokus uživateli.
            self._offscreen = True
            self._park()

    def _wants_background(self):
        return config.PLATFORM == "mac" and config.BACKGROUND and not config.MOCK

    def _background_mode(self):
        return self._wants_background() and self.driver is not None

    def close(self):
        if self.driver:
            try:
                self._restore_desktop()
                self.driver.quit()
            finally:
                self._stop_wda()
                # Ukončení relace mac2 appku někdy znovu vytáhne do popředí.
                self._restore_desktop()

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

    def _park(self):
        """Schová okno a vrátí fokus uživateli. Kurzorem nehýbe."""
        if not self._background_mode() or not self._offscreen:
            return
        import mac_background

        try:
            mac_background.hide_app(config.LIFTAGO_BUNDLE_ID, self._user_front)
        except Exception:  # noqa: BLE001
            log.warning("Liftago se nepodařilo schovat", exc_info=True)

    def _restore_desktop(self):
        if not self._wants_background():
            return
        import mac_background

        self._offscreen = False
        pos = self._saved_window or (100, 80)
        try:
            mac_background.place(
                config.LIFTAGO_BUNDLE_ID, pos[0], pos[1], self._user_front,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Okno Liftaga se nepodařilo vrátit: %s", exc)

    def _restart_app(self):
        d = self.driver
        if config.PLATFORM == "mac":
            if self._background_mode():
                self._restart_background()
                return
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

    def _restart_background(self):
        """Nechá Liftago aplikací, kterou session hledá, a nenechá ji v popředí.

        Ukončení appky by session odpojilo a další hledání by šlo do Finderu (404).
        Okno se proto neukončuje. Když ho schování skryje před hledáním, ukáže se
        za ostatními aplikacemi a kurzor se neposouvá.
        """
        import mac_background

        d = self.driver
        self._offscreen = True
        if self._saved_window is None:
            self._saved_window = mac_background.window_position(config.LIFTAGO_BUNDLE_ID)
        try:
            mac_background.show_unfocused(config.LIFTAGO_BUNDLE_ID, self._user_front)
        except Exception:  # noqa: BLE001
            log.warning("Okno Liftaga se nepodařilo nechat v pozadí", exc_info=True)
        try:
            _find(d, SELECTORS["open_search"], timeout=12)
        except Exception as first:  # noqa: BLE001
            log.info("Skrytý start ještě nevidí úvod, okno nechám v pozadí")
            label = _exact_label(SELECTORS["open_search"])
            if not label or not mac_background.element_exists(config.LIFTAGO_BUNDLE_ID, label):
                raise first
            log.info("Úvod vidí zpřístupnění, mačkám %s bez fokusu", label)
            mac_background.press(config.LIFTAGO_BUNDLE_ID, label)
            self._intro_done = True
        self._park()

    def _element_names(self, el):
        names = []
        for attr in ("label", "identifier", "title", "name"):
            try:
                value = el.get_attribute(attr)
            except Exception:  # noqa: BLE001
                value = None
            if value and value.strip() and value.strip() not in names:
                names.append(value.strip())
        return names

    def _element_wanted(self, el):
        names = self._element_names(el)
        return names[0] if names else ""

    def _press(self, el):
        """Stiskne prvek. V pozadí přes AXPress, jinak kliknutím."""
        names = self._element_names(el)
        if any(is_order_button(name) for name in names):
            raise RuntimeError("Bot nesmí stisknout Objednat")
        if not self._background_mode():
            el.click()
            return
        if not names:
            raise RuntimeError("Prvek nemá popisek, v pozadí ho nelze stisknout")
        import mac_background

        self._park()
        last = None
        for name in names:
            try:
                mac_background.press(config.LIFTAGO_BUNDLE_ID, name)
            except Exception as e:  # noqa: BLE001
                last = e
                continue
            self._park()
            return
        self._park()
        raise last

    def _activate_odvoz_row(self, row):
        """Vybere Odvoz vozu jedním zásahem na název, ne na sloučený popisek řádku.

        Dlouhý popisek AXPress přijme a kategorii nevybere. V popředí se klepne
        na řádek jako dřív. V pozadí se vezme prvek „Odvoz vozu“ v tom řádku.
        """
        if not self._background_mode():
            self._press(row)
            return
        import mac_background

        sel = SELECTORS.get("category_row") or {}
        title = (sel.get("ax_title") or "Odvoz vozu").strip()
        context = (sel.get("ax_context") or "Řidič vás odveze").strip()
        try:
            mac_background.show_unfocused(config.LIFTAGO_BUNDLE_ID, self._user_front)
        except Exception:  # noqa: BLE001
            log.warning("Okno Liftaga se před výběrem kategorie nepodařilo ukázat", exc_info=True)
        time.sleep(0.4)
        try:
            mac_background.activate_in_row(config.LIFTAGO_BUNDLE_ID, title, context)
        finally:
            self._park()

    def _press_suggestion(self, el):
        """První návrh adresy. Nemusí mít popisek: je to tlačítko za „Vybrat na mapě“."""
        if not self._background_mode():
            self._press(el)
            return
        raw_value = ""
        try:
            raw_value = el.get_attribute("value") or ""
        except Exception:  # noqa: BLE001
            raw_value = ""
        target = suggestion_target(self._element_names(el), raw_value, SELECTORS.get("first_suggestion"))
        if target[0] == "label":
            self._press(el)
            return
        import mac_background

        self._park()
        if target[0] != "after":
            sel = SELECTORS.get("first_suggestion") or {}
            target = (
                "after",
                (sel.get("ax_after") or "").strip(),
                (sel.get("ax_role") or "AXButton").strip() or "AXButton",
                int(sel.get("ax_nth") or 1),
                target[1],
            )
        _kind, marker, role, nth, val = target
        if not marker:
            raise RuntimeError("Prvek nemá popisek, v pozadí ho nelze stisknout")
        mac_background.press_following(config.LIFTAGO_BUNDLE_ID, marker, role, nth, val)
        self._park()

    def _set_value(self, el, text):
        wanted = self._element_wanted(el)
        if not wanted:
            raise RuntimeError("Textové pole nemá popisek")
        import mac_background

        self._park()
        mac_background.set_value(config.LIFTAGO_BUNDLE_ID, wanted, text)
        self._park()

    def _type_to_process(self, el, text):
        """Záložní zápis adresy přímo do procesu, když hodnota pole nespustí našeptávač."""
        import mac_background

        wanted = self._element_wanted(el)
        mac_background.focus_element(config.LIFTAGO_BUNDLE_ID, wanted, self._user_front)
        mac_background.type_to_pid(config.LIFTAGO_BUNDLE_ID, text)
        self._park()

    def _clear_focused_field(self):
        if config.PLATFORM == "mac" and not self._background_mode():
            # Cmd+A a Delete jen když běžíme v popředí (modifierFlags: Command = 16).
            # V pozadí by zkratka smazala text v aplikaci, kterou má uživatel otevřenou.
            try:
                self.driver.execute_script(
                    "macos: keys", {"keys": [{"key": "a", "modifierFlags": 16}, "\ue017"]})
            except Exception:  # noqa: BLE001
                pass

    def _type_and_pick(self, input_key, text):
        """Do (už otevřeného) pole napíše adresu a vybere první návrh z našeptávače."""
        d = self.driver
        inp = _find(d, SELECTORS[input_key])
        if self._background_mode():
            try:
                self._set_value(inp, text)
                suggestion = _find(d, SELECTORS["first_suggestion"], timeout=4)
            except Exception:  # noqa: BLE001
                log.info("Našeptávač po nastavení hodnoty nevyšel, píšu přímo do procesu")
                self._type_to_process(inp, text)
                suggestion = _find(d, SELECTORS["first_suggestion"], timeout=8)
        else:
            inp.click()
            if config.PLATFORM == "mac":
                self._clear_focused_field()
            else:
                inp.clear()
            inp.send_keys(text)
            time.sleep(2)  # počkat na našeptávač
            suggestion = _find(d, SELECTORS["first_suggestion"])
        self._press_suggestion(suggestion)
        time.sleep(1.5)

    def _tap_optional(self, key, timeout=4):
        """Klikne na prvek, pokud se objeví (např. popup 'Pro koho je tato jízda')."""
        try:
            self._press(_find(self.driver, SELECTORS[key], timeout=timeout))
            time.sleep(1.5)
            return True
        except RuntimeError as e:
            if "Objednat" in str(e):
                raise
            log.info("Volitelný prvek %s se nepodařilo stisknout: %s", key, e)
            return False
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

    def _odvoz_selected(self):
        sel = SELECTORS.get("category_selected")
        if not sel:
            return any(label_is_selected_odvoz(t) for t in _screen_texts(self.driver))
        try:
            el = _find(self.driver, sel, timeout=1.5)
        except Exception:  # noqa: BLE001
            return False
        parts = []
        for attr in ("label", "title", "value", "text", "name"):
            try:
                value = el.get_attribute(attr)
            except Exception:  # noqa: BLE001
                value = None
            if value:
                parts.append(value)
        return label_is_selected_odvoz(" ".join(parts))

    def _select_odvoz(self):
        """Klikne Odvoz vozu a počká, až bude vybraná. Tlačítko řidičů samo nestačí.

        Řádek se stiskne jednou. Druhý stisk vybrané kategorie otevírá info o službě.
        Hotovo je až když popisek začíná „vybráno“ a obsahuje Odvoz vozu, nebo když
        tlačítko samo jmenuje Odvoz vozu.
        """
        deadline = time.time() + SELECTORS.get("go_button_wait_seconds", 12)
        presses = 0
        while time.time() < deadline:
            step = selection_step(assess_category_screen(_screen_texts(self.driver)), presses)
            if step == "done" or self._odvoz_selected():
                return True
            if step == "stop":
                return False
            if step == "press_row":
                row = self._find_category_row()
                if row is None:
                    return False
                label = row.get_attribute("label") or ""
                if "Pouze později" in label and "Odvoz vozu" in label:
                    return False
                if not label_is_selected_odvoz(label) and not label.lower().startswith("vybráno"):
                    self._activate_odvoz_row(row)
                    presses += 1
            time.sleep(1)
        return (
            selection_step(assess_category_screen(_screen_texts(self.driver)), presses) == "done"
            or self._odvoz_selected()
        )

    def _collect_offers(self):
        """Počká OFFERS_WAIT s a sesbírá všechny ceny nabídek řidičů (i ty, co mezitím vyprší).

        Nikdy neklikáme na 'Objednat'. Vrací (poznámka, [ceny])."""
        self._park()
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
            self._press(_find(self.driver, SELECTORS["close_offers"], timeout=5))
            time.sleep(1.5)
            if "cancel_confirm" in SELECTORS:
                self._tap_optional("cancel_confirm", timeout=3)
        except Exception as e:  # noqa: BLE001
            log.warning("Nepodařilo se zavřít obrazovku nabídek: %s", e)

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
            if not self._intro_done:
                self._press(_find(d, SELECTORS["open_search"]))  # 'Kam pojedeme?'
            time.sleep(1.5)
            self._press(_find(d, SELECTORS["pickup_field"]))  # adresa vyzvednutí
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
            # Nejdřív klik na Odvoz vozu. Tlačítko řidičů až po potvrzení výběru.
            if not self._select_odvoz():
                self._dump_debug("go_button_missing")
                step = assess_category_screen(_screen_texts(d))
                if step == "wrong_category":
                    missing = "tlačítko 'Přejít k výběru řidiče' patří jiné kategorii"
                elif step in ("select", "wait"):
                    missing = "Odvoz vozu není vybraná kategorie, tlačítko 'Přejít k výběru řidiče' nepoužito"
                else:
                    missing = "tlačítko 'Přejít k výběru řidiče' nenalezeno"
                return {"ts": now, "km": km, "eta": eta, "bez": bez, "se": NO_RESULT, "note": missing}
            try:
                go = _find(d, SELECTORS["go_to_drivers"], timeout=SELECTORS.get("go_button_wait_seconds", 12))
            except Exception:  # noqa: BLE001
                self._dump_debug("go_button_missing")
                return {"ts": now, "km": km, "eta": eta, "bez": bez, "se": NO_RESULT,
                        "note": "tlačítko 'Přejít k výběru řidiče' nenalezeno"}
            go_label = go.get_attribute("label") or ""
            confirmed = go_label_names_odvoz(go_label) or self._odvoz_selected() or any(
                label_is_selected_odvoz(text) for text in _screen_texts(d)
            )
            if go_label_names_other_category(go_label) or not confirmed or not go_label_targets_odvoz(go_label):
                self._dump_debug("go_button_missing")
                if go_label_names_other_category(go_label):
                    refused = "tlačítko 'Přejít k výběru řidiče' patří jiné kategorii"
                else:
                    refused = "Odvoz vozu není vybraná kategorie, tlačítko 'Přejít k výběru řidiče' nepoužito"
                return {"ts": now, "km": km, "eta": eta, "bez": bez, "se": NO_RESULT, "note": refused}
            self._press(go)
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
