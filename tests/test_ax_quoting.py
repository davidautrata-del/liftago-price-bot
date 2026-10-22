"""Adresy a popisky nesmí rozbít AppleScript (chyba -2741 z 12:49).

Hledání prvku už nejde přes osascript (timeout 45 s z 13:13). Strom oken se
prochází jen do první shody a menu bar se neotevírá.
"""
import time
import unittest
from pathlib import Path

import mac_background
from mac_background import (
    ACTION_SCRIPT,
    AX_ATTRIBUTE_NAMES,
    AxError,
    applescript_literal,
    build_script,
    category_click_target,
    category_hit,
    best_after,
    nth_after,
    pick_suggestion,
    press,
    row_containing,
    search_tree,
    title_click_point,
)

ADDRESS = "Letecké muzeum Kbely, Mladoboleslavská, Praha 9"


def _ax_args_line(src):
    for line in src.splitlines():
        if line.strip().startswith("set axArgs to"):
            return line
    raise AssertionError("chybí set axArgs")


def _quotes_balanced(line):
    in_str = False
    i = 0
    while i < len(line):
        ch = line[i]
        if in_str:
            if ch == "\\" and i + 1 < len(line):
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        i += 1
    return not in_str


def _search(roots, wanted, deadline=None, accept_exact=None):
    described = []

    def describe(node):
        described.append(node.get("desc") or node.get("role"))
        return node

    def children_of(node):
        return node.get("children") or ()

    found = search_tree(
        roots, wanted, children_of, describe,
        deadline if deadline is not None else time.monotonic() + 2,
        accept_exact=accept_exact,
    )
    return found, described


class AxQuotingTest(unittest.TestCase):
    def test_attribute_names_are_strings_not_dll_symbols(self):
        self.assertEqual(AX_ATTRIBUTE_NAMES["windows"], "AXWindows")
        self.assertEqual(AX_ATTRIBUTE_NAMES["press"], "AXPress")
        self.assertEqual(AX_ATTRIBUTE_NAMES["children"], "AXChildren")
        self.assertEqual(AX_ATTRIBUTE_NAMES["placeholder"], "AXPlaceholderValue")
        file_src = Path(mac_background.__file__).read_text(encoding="utf-8")
        self.assertNotIn("kAX", file_src)
        self.assertNotIn("in_dll(self.ax", file_src)

    def test_shell_script_does_not_walk_the_app(self):
        self.assertNotIn("entire contents", ACTION_SCRIPT)
        self.assertNotIn("AXIdentifier", ACTION_SCRIPT)
        self.assertNotIn("AXPress", ACTION_SCRIPT)

    def test_kbely_address_stays_inside_one_string(self):
        src = build_script(["com.example.app", "set", "Cílová adresa", ADDRESS])
        line = _ax_args_line(src)
        self.assertNotIn("%%AX_ARGS%%", src)
        self.assertIn(f'("{ADDRESS}")', line)
        self.assertTrue(_quotes_balanced(line))

    def test_quote_comma_backslash_and_newline_are_escaped(self):
        nasty = 'Řekni "ahoj", pak \\ konec\nřádek'
        lit = applescript_literal(nasty)
        self.assertIn('\\"', lit)
        self.assertIn("\\\\", lit)
        self.assertIn(" & return & ", lit)
        self.assertNotIn("\n", lit)
        line = _ax_args_line(build_script(["bid", "press", nasty]))
        self.assertTrue(_quotes_balanced(line))
        self.assertNotIn('Řekni "ahoj"', line)

    def test_order_button_is_refused_without_a_script(self):
        with self.assertRaises(AxError):
            press("com.example.app", "Objednat")

    def test_menu_bar_is_not_searched(self):
        window = {
            "role": "AXWindow",
            "children": [
                {
                    "role": "AXMenuBar",
                    "desc": "lišta",
                    "children": [{"role": "AXMenuItem", "desc": "Nástupní adresa"}],
                },
                {"role": "AXTextField", "desc": "Nástupní adresa"},
                {"role": "AXButton", "desc": "až potom"},
            ],
        }
        found, described = _search([window], "Nástupní adresa")
        self.assertEqual(found["role"], "AXTextField")
        self.assertEqual(described.count("Nástupní adresa"), 1)
        self.assertNotIn("až potom", described)

    def test_menu_label_alone_is_not_a_match(self):
        window = {
            "role": "AXWindow",
            "children": [
                {"role": "AXMenuBar", "children": [{"role": "AXMenuItem", "desc": "Nástupní adresa"}]},
            ],
        }
        with self.assertRaises(AxError):
            _search([window], "Nástupní adresa")

    def test_deadline_stops_before_a_full_scan(self):
        window = {"role": "AXWindow", "children": [{"role": "AXButton", "desc": "Nástupní adresa"}]}
        with self.assertRaises(AxError) as caught:
            _search([window], "Nástupní adresa", deadline=time.monotonic() - 1)
        self.assertIn("limit času", str(caught.exception))

    def test_category_hit_is_the_title_not_the_combined_row(self):
        window = {
            "role": "AXWindow",
            "children": [
                {"role": "AXGroup", "desc": "jinde", "children": [
                    {"role": "AXStaticText", "desc": "Odvoz vozu"},
                ]},
                {
                    "role": "AXGroup",
                    "desc": "Odvoz vozu Řidič vás odveze ve vašem voze Odhad ceny 565 Kč za 19 min",
                    "children": [
                        {"role": "AXStaticText", "desc": "Odvoz vozu"},
                        {"role": "AXStaticText", "desc": "565 Kč"},
                        {"role": "AXStaticText", "desc": "Řidič vás odveze ve vašem voze"},
                        {"role": "AXStaticText", "desc": "za 19 min"},
                    ],
                },
                {"role": "AXButton", "desc": "Přejít k výběru řidiče"},
                {"role": "AXButton", "desc": "Objednat"},
            ],
        }
        found = category_hit(
            [window], "Odvoz vozu", "Řidič vás odveze",
            lambda node: node.get("children") or (),
            lambda node: node,
            time.monotonic() + 2,
        )
        self.assertEqual(found["role"], "AXStaticText")
        self.assertEqual(found["desc"], "Odvoz vozu")
        self.assertNotIn("Odhad ceny", found["desc"])

    def test_category_hit_prefers_the_button_named_odvoz(self):
        title = {"role": "AXStaticText", "desc": "Odvoz vozu"}
        button = {
            "role": "AXButton",
            "desc": "Odvoz vozu",
            "children": [
                title,
                {"role": "AXStaticText", "desc": "Řidič vás odveze ve vašem voze"},
            ],
        }
        found = category_hit(
            [{"role": "AXWindow", "children": [button]}],
            "Odvoz vozu",
            "Řidič vás odveze",
            lambda node: node.get("children") or (),
            lambda node: node,
            time.monotonic() + 2,
        )
        self.assertIs(found, button)

    def test_combined_row_click_aims_at_the_title(self):
        group = {
            "role": "AXGroup",
            "desc": "Odvoz vozu Řidič vás odveze ve vašem voze Odhad ceny 565 Kč za 19 min",
        }
        window = {
            "role": "AXWindow",
            "children": [
                group,
                {"role": "AXButton", "desc": "Přejít k výběru řidiče"},
            ],
        }
        found = row_containing(
            [window], "Odvoz vozu", "Řidič vás odveze",
            lambda node: node.get("children") or (),
            lambda node: node,
            time.monotonic() + 2,
        )
        self.assertIs(found, group)
        x, y = title_click_point(1003, -820, 1006, 63, False)
        self.assertLess(x - 1003, 120)
        self.assertLess(y - (-820), 30)
        self.assertNotAlmostEqual(x, 1003 + 503, delta=50)

    def test_missing_title_uses_the_row_that_was_read(self):
        row = {
            "label": "Odvoz vozu Řidič vás odveze ve vašem voze Odhad ceny 597 Kč za 15 min",
            "x": 1003, "y": 240, "width": 1006, "height": 63,
        }
        title = {"label": "Odvoz vozu", "x": 1067, "y": 256, "width": 68, "height": 16}
        x, y = category_click_target([
            row,
            {"label": "597 Kč", "x": 1600, "y": 250, "width": 70, "height": 18},
            {"label": "Přejít k výběru řidiče", "x": 1100, "y": 900, "width": 400, "height": 48},
            {"label": "Objednat", "x": 1100, "y": 960, "width": 200, "height": 48},
            title,
        ])
        self.assertAlmostEqual(x, 1067 + 34)
        self.assertAlmostEqual(y, 256 + 8)
        x, y = category_click_target([row])
        self.assertLess(x - 1003, 120)
        self.assertLess(y - 240, 30)
        self.assertNotAlmostEqual(x, 1003 + 503, delta=50)
        with self.assertRaises(AxError):
            category_click_target([
                {"label": "Přejít k výběru řidiče", "x": 1, "y": 1, "width": 10, "height": 10},
            ])

    def test_category_click_does_not_move_the_pointer(self):
        src = Path(mac_background.__file__).read_text(encoding="utf-8")
        self.assertIn("CGEventPostToPid", src)
        self.assertIn("CGEventSourceCreate", src)
        self.assertNotIn("CGEventPost(", src)
        self.assertNotIn("CGWarpMouseCursorPosition", src)
        self.assertNotIn("kAX", src)

    def test_press_prefers_a_button_over_a_static_label(self):
        window = {
            "role": "AXWindow",
            "children": [
                {"role": "AXStaticText", "desc": "Nástupní adresa"},
                {"role": "AXTextField", "desc": "Nástupní adresa", "placeholder": "Nástupní adresa"},
            ],
        }
        found, _described = _search(
            [window],
            "Nástupní adresa",
            accept_exact=lambda info: info.get("role") in {"AXTextField", "AXButton"},
        )
        self.assertEqual(found["role"], "AXTextField")

    def test_unlabeled_suggestion_is_the_button_after_the_marker(self):
        window = {
            "role": "AXWindow",
            "children": [
                {
                    "role": "AXMenuBar",
                    "children": [{"role": "AXButton", "desc": "Vybrat na mapě"}],
                },
                {
                    "role": "AXButton",
                    "desc": "Vybrat na mapě",
                    "children": [{"role": "AXButton", "desc": "vnitřní"}],
                },
                {"role": "AXButton", "desc": "", "value": ""},
                {"role": "AXButton", "desc": "druhý návrh"},
            ],
        }
        found, described = _after([window], "Vybrat na mapě", nth=1)
        self.assertEqual(found.get("desc"), "")
        self.assertNotEqual(found.get("desc"), "vnitřní")
        self.assertNotIn("druhý návrh", described)

    def test_suggestion_value_beats_the_blank_button(self):
        window = {
            "role": "AXWindow",
            "children": [
                {"role": "AXButton", "desc": "Vybrat na mapě"},
                {"role": "AXButton", "desc": "", "value": ""},
                {"role": "AXButton", "desc": "", "value": "Letenské náměstí 1"},
            ],
        }
        found, _described = _after([window], "Vybrat na mapě", nth=1, value="Letenské náměstí 1")
        self.assertEqual(found.get("value"), "Letenské náměstí 1")

    def test_destination_suggestion_is_not_the_blank_first_button(self):
        window = {
            "role": "AXWindow",
            "children": [
                {"role": "AXButton", "desc": "Vybrat na mapě"},
                {"role": "AXButton", "desc": ""},
                {"role": "AXButton", "desc": "Aviatická 1, Letiště Václava Havla (PRG), Praha 6, Česko"},
                {"role": "AXButton", "desc": "Vítězné náměstí, Praha 6, Česko"},
                {"role": "AXButton", "desc": "Objednat"},
            ],
        }
        found = best_after(
            [window],
            "Vybrat na mapě",
            "Vítězné náměstí 1, Praha 6",
            lambda node: node.get("children") or (),
            lambda node: node,
            time.monotonic() + 2,
        )
        self.assertEqual(found["desc"], "Vítězné náměstí, Praha 6, Česko")
        self.assertEqual(
            pick_suggestion("Vítězné náměstí 1, Praha 6", ["", found["desc"]]),
            found["desc"],
        )


def _after(roots, marker, nth=1, value=""):
    described = []

    def describe(node):
        described.append(node.get("desc") if node.get("desc") is not None else node.get("role"))
        return node

    found = nth_after(
        roots, marker, "AXButton", nth,
        lambda node: node.get("children") or (),
        describe,
        time.monotonic() + 2,
        value=value,
    )
    return found, described


if __name__ == "__main__":
    unittest.main()
