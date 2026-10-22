"""Tlačítko „Přejít k výběru řidiče“ proti uložené hierarchii z 22. 10. 2026."""
import json
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import mac_background
from mac_background import pick_suggestion
from liftago_driver import (
    _exact_label,
    assess_category_screen,
    failure_line,
    go_label_targets_odvoz,
    is_order_button,
    label_is_selected_odvoz,
    labels_after_marker,
    missing_odvoz_note,
    selection_step,
    suggestion_target,
)

ROOT = Path(__file__).resolve().parents[1]
DUMP = ROOT / "debug" / "20261022_104003_go_button_missing.xml"
OLD = "label BEGINSWITH 'Přejít k výběru řidiče' AND label ENDSWITH 'Odvoz vozu'"
_OPS = ("BEGINSWITH", "ENDSWITH", "CONTAINS", "==")


def _split_top(expr, op):
    parts, buf, quote, i = [], [], False, 0
    token = f" {op} "
    while i < len(expr):
        if expr[i] == "'":
            quote = not quote
            buf.append(expr[i])
            i += 1
            continue
        if not quote and expr.startswith(token, i):
            parts.append("".join(buf))
            buf = []
            i += len(token)
            continue
        buf.append(expr[i])
        i += 1
    parts.append("".join(buf))
    return parts


class _EmptySelenium404(Exception):
    """Stejný tvar jako TimeoutException, která v listu nechala jen 'Message:'."""

    def __str__(self):
        return (
            "Message:\n"
            "Stacktrace:\n"
            "ProxyRequestError: Request failed with status code 404\n"
            "    at WDAMacProxy.proxy\n"
        )


def matches(predicate, attrs):
    return any(_match_and(part, attrs) for part in _split_top(predicate, "OR"))


def _match_and(expr, attrs):
    return all(_match_cmp(part.strip(), attrs) for part in _split_top(expr, "AND"))


def _match_cmp(expr, attrs):
    for op in _OPS:
        token = f" {op} "
        if token not in expr:
            continue
        left, right = expr.split(token, 1)
        left = left.strip()
        if left not in ("label", "title", "value", "identifier"):
            raise AssertionError(left)
        if not (right.startswith("'") and right.endswith("'")):
            raise AssertionError(right)
        val = right[1:-1]
        got = attrs.get(left, "") or ""
        if op == "==":
            return got == val
        if op == "BEGINSWITH":
            return got.startswith(val)
        if op == "ENDSWITH":
            return got.endswith(val)
        if op == "CONTAINS":
            return val in got
    raise AssertionError(expr)


def _elements(path):
    root = ET.parse(path).getroot()
    for el in root.iter():
        yield el.attrib


class GoButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mac = json.loads((ROOT / "selectors.mac.json").read_text())
        cls.elements = list(_elements(DUMP))
        cls.labels = [
            el.get("label", "") for el in cls.elements if el.get("label", "").strip()
        ]

    def test_predicates_do_not_use_type(self):
        for key, sel in self.mac.items():
            if isinstance(sel, dict) and sel.get("by") == "predicate":
                self.assertIsNone(re.search(r"(^|\s)type(\s|$)", sel["value"]), key)

    def test_saved_screen_matches_new_selector_not_old(self):
        new = self.mac["go_to_drivers"]["value"]
        found_new = [el for el in self.elements if matches(new, el)]
        found_old = [el for el in self.elements if matches(OLD, el)]
        self.assertEqual(len(found_new), 1)
        self.assertEqual(found_new[0].get("label"), "Přejít k výběru řidiče")
        self.assertEqual(found_old, [])

    def test_saved_screen_has_odvoz_row_but_not_selected(self):
        selected = self.mac["category_selected"]["value"]
        self.assertEqual([el for el in self.elements if matches(selected, el)], [])
        self.assertTrue(any("Odvoz vozu Řidič" in label for label in self.labels))
        self.assertTrue(any(label.startswith("vybráno Liftago") for label in self.labels))

    def test_go_button_waits_until_odvoz_is_selected(self):
        self.assertIn("Přejít k výběru řidiče", self.labels)
        self.assertEqual(assess_category_screen(self.labels), "select")
        self.assertEqual(selection_step("select", 0), "press_row")
        self.assertEqual(selection_step("select", 1), "wait")
        self.assertEqual(assess_category_screen(["Přejít k výběru řidiče"]), "absent")
        self.assertEqual(
            assess_category_screen(["Přejít k výběru řidiče", "vybráno Liftago Všechny dostupné vozy"]),
            "absent",
        )
        selected = [
            "vybráno Odvoz vozu Řidič vás odveze ve vašem voze" if label.startswith("vybráno") else label
            for label in self.labels
        ]
        self.assertEqual(assess_category_screen(selected), "press")
        self.assertEqual(selection_step("press", 0), "done")
        self.assertEqual(assess_category_screen(["Přejít k výběru řidiče · Odvoz vozu"]), "press")

    def test_other_category_suffix_is_refused(self):
        self.assertTrue(go_label_targets_odvoz("Přejít k výběru řidiče"))
        self.assertTrue(go_label_targets_odvoz("Přejít k výběru řidiče · Odvoz vozu"))
        self.assertFalse(go_label_targets_odvoz("Přejít k výběru řidiče · Liftago"))
        self.assertEqual(
            assess_category_screen(["Přejít k výběru řidiče · Liftago"]),
            "wrong_category",
        )

    def test_order_button_is_never_a_go_button(self):
        self.assertTrue(is_order_button("Objednat"))
        self.assertTrue(is_order_button("Objednat za 545 Kč"))
        self.assertFalse(is_order_button("Přejít k výběru řidiče"))
        self.assertFalse(go_label_targets_odvoz("Objednat"))
        self.assertTrue(label_is_selected_odvoz("vybráno Odvoz vozu Řidič vás odveze"))
        self.assertFalse(label_is_selected_odvoz("vybráno Liftago Všechny dostupné vozy"))

    def test_background_actions_do_not_use_mouse_or_system_keys(self):
        src = mac_background.ACTION_SCRIPT
        self.assertNotIn("keystroke", src)
        self.assertNotIn("key code", src)
        self.assertNotIn("click ", src)
        self.assertNotIn("activate", src.lower())
        self.assertNotIn("entire contents", src)
        self.assertIn("set visible of proc to false", src)
        self.assertIn("set frontmost of proc to false", src)
        typed = Path(mac_background.__file__).read_text()
        self.assertIn("AXPress", typed)
        self.assertIn("CGEventPostToPid", typed)
        self.assertNotIn("CGWarpMouseCursorPosition", typed)
        self.assertNotIn("entire contents", typed)
        self.assertNotIn("keystroke", typed)
        background = Path(ROOT / "liftago_driver.py").read_text().split("def _restart_background", 1)[1].split("def _element_names", 1)[0]
        self.assertNotIn("activateApp", background)
        self.assertNotIn("terminateApp", background)
        self.assertNotIn("launchApp", background)
        self.assertIn("show_unfocused", background)
        select = Path(ROOT / "liftago_driver.py").read_text().split("def _select_odvoz", 1)[1].split("def _collect_offers", 1)[0]
        self.assertIn("_activate_odvoz_row", select)
        self.assertNotIn("_press(row)", select)
        self.assertEqual(self.mac["category_row"]["ax_title"], "Odvoz vozu")
        self.assertIn("Řidič", self.mac["category_row"]["ax_context"])
        session = Path(ROOT / "liftago_driver.py").read_text()
        self.assertIn("skipAppKill", session)

    def test_hidden_intro_label_comes_from_the_selector(self):
        self.assertEqual(_exact_label(self.mac["open_search"]), "Kam pojedeme?")

    def test_unlabeled_suggestion_uses_the_button_after_the_marker(self):
        sel = self.mac["first_suggestion"]
        self.assertEqual(sel["ax_after"], "Vybrat na mapě")
        self.assertEqual(sel["ax_nth"], 1)
        self.assertEqual(suggestion_target([], "", sel), ("after", "Vybrat na mapě", "AXButton", 1, ""))
        self.assertEqual(
            suggestion_target([], "Letenské náměstí 1", sel),
            ("after", "Vybrat na mapě", "AXButton", 1, "Letenské náměstí 1"),
        )
        self.assertEqual(suggestion_target(["Nástupní adresa"], "", sel)[0], "label")
        typed = "Vítězné náměstí 1, Praha 6"
        labels = [
            "",
            "Aviatická 1, Letiště Václava Havla (PRG), Praha 6, Česko",
            "Vítězné náměstí, Praha 6, Česko",
        ]
        self.assertEqual(pick_suggestion(typed, labels), "Vítězné náměstí, Praha 6, Česko")
        self.assertIsNone(pick_suggestion(typed, ["", "Vybrat na mapě"]))
        letnany = "OC Letňany, Veselská 663, Praha 9"
        tenant = "Bistro DELMART OC Letňany, Veselská 663, Praha 18, Česko"
        mall = "OC Letňany, Veselská 663, Praha 9, Česko"
        self.assertEqual(pick_suggestion(letnany, [tenant, mall]), mall)
        self.assertIsNone(pick_suggestion(letnany, ["", tenant]))
        self.assertEqual(
            pick_suggestion(
                "Letiště Václava Havla, Aviatická 1, Praha 6",
                ["Aviatická 1, Letiště Václava Havla (PRG), Praha 6, Česko"],
            ),
            "Aviatická 1, Letiště Václava Havla (PRG), Praha 6, Česko",
        )
        page = """
        <XCUIElementTypeWindow>
          <XCUIElementTypeButton label="Vybrat na mapě"/>
          <XCUIElementTypeButton label=""/>
          <XCUIElementTypeButton label="Vítězné náměstí, Praha 6, Česko"/>
        </XCUIElementTypeWindow>
        """
        self.assertEqual(labels_after_marker(page, "Vybrat na mapě")[1], "Vítězné náměstí, Praha 6, Česko")
        self.assertEqual(
            missing_odvoz_note(["Vybrat na mapě", "Cílová adresa"]),
            "seznam kategorií se neotevřel, návrh adresy se nepodařilo vybrat",
        )
        self.assertEqual(
            missing_odvoz_note(["Liftago Všechny dostupné vozy Odhad ceny 250 Kč", "Přejít k výběru řidiče"]),
            "Odvoz vozu v nabídce není",
        )
        with self.assertRaises(RuntimeError):
            suggestion_target(["Objednat"], "", sel)

    def test_sheet_note_keeps_the_real_404(self):
        exc = _EmptySelenium404()
        line = failure_line(exc)
        note = f"CHYBA: {type(exc).__name__}: {line}"[:300]
        self.assertEqual(line, "ProxyRequestError: Request failed with status code 404")
        self.assertIn("404", note)
        self.assertNotIn("Message:", note)


if __name__ == "__main__":
    unittest.main()
