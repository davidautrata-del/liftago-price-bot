"""Kam se řádek měření posílá: stejný sešit, list podle gid."""
import unittest

import config
from sink import HEADER, webhook_payload

SHEET = "1B4KfTAtr59zif3DZ6WGJ2j-pNOw4MvohOABXnoZryC8"
GID = 1586305980


class SheetTargetTest(unittest.TestCase):
    def test_defaults_match_the_existing_tab(self):
        self.assertEqual(config.SHEET_ID, SHEET)
        self.assertEqual(config.SHEET_GID, GID)
        self.assertEqual(config.SHEET_TAB, "Data")

    def test_webhook_payload_selects_that_tab(self):
        row = ["2026-10-22 09:49", "A", "B", 1.7, 15, 550.0, "žádný výsledek", "poznámka"]
        body = webhook_payload([row])
        self.assertEqual(body["gid"], GID)
        self.assertEqual(body["rows"], [row])
        self.assertEqual(len(row), len(HEADER))
        self.assertEqual(
            HEADER,
            [
                "Date & time", "From", "To", "Distance (km)", "ETA (min)",
                "Price without discount (CZK)", "Driver offers (CZK)", "Note",
            ],
        )


if __name__ == "__main__":
    unittest.main()
