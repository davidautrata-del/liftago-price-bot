"""Adresy a popisky nesmí rozbít AppleScript (chyba -2741 z 12:49)."""
import unittest

from mac_background import ACTION_SCRIPT, applescript_literal, build_script

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


class AxQuotingTest(unittest.TestCase):
    def test_identifier_quote_is_inside_system_events(self):
        handler = ACTION_SCRIPT.split("on attrText", 1)[1].split("end attrText", 1)[0]
        self.assertLess(handler.index('tell application "System Events"'), handler.index("AXIdentifier"))

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


if __name__ == "__main__":
    unittest.main()
