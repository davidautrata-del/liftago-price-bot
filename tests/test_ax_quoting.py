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
    nth_after,
    press,
    search_tree,
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
