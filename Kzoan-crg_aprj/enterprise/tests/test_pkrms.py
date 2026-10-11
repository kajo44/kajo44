# -*- coding: utf-8 -*-
"""Tes regresi ringan untuk normalisasi label kondisi jalan."""
import unittest

from aprj_pkrms import normalize_condition


class NormalizeConditionTests(unittest.TestCase):
    def test_standard_labels(self):
        cases = {
            "BAIK": "baik",
            "kondisi baik": "baik",
            "rusak ringan": "rusak_ringan",
            "RUSAK-SEDANG": "rusak_sedang",
            "rusak_berat": "rusak_berat",
            "heavy damage": "rusak_berat",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(normalize_condition(source), expected)

    def test_negative_or_ambiguous_positive_phrases_are_not_misclassified(self):
        for source in ("tidak baik", "kurang baik", "belum baik", "tidak layak", "tidak aman", "tidak buruk", "belum rusak berat"):
            with self.subTest(source=source):
                self.assertIsNone(normalize_condition(source))

    def test_unknown_values_remain_unclassified(self):
        for source in ("", None, "perlu survei ulang", "rusak tidak diketahui"):
            with self.subTest(source=source):
                self.assertIsNone(normalize_condition(source))


if __name__ == "__main__":
    unittest.main()
