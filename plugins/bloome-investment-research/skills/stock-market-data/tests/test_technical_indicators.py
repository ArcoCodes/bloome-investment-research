from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from technical_indicators import (  # noqa: E402
    calculate_bollinger_bands,
    calculate_extended_indicators,
    calculate_kdj,
    calculate_mfi,
    calculate_obv,
    calculate_rolling_vwap,
)


def frame(closes: np.ndarray, volumes: np.ndarray | None = None) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    volume = np.asarray(volumes if volumes is not None else np.full(len(close), 100.0), dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": volume,
        },
        index=pd.date_range("2026-01-01", periods=len(close), freq="D"),
    )


class ExtendedIndicatorTests(unittest.TestCase):
    def test_obv_rising_price_and_volume_is_confirmed(self):
        result = calculate_obv(frame(np.arange(1.0, 31.0)))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["value"], 2900.0)
        self.assertEqual(result["trend"], "rising")
        self.assertEqual(result["price_confirmation"], "confirmed")

    def test_rolling_vwap_uses_typical_price_and_volume(self):
        df = frame(np.full(20, 10.0), np.arange(1.0, 21.0))
        result = calculate_rolling_vwap(df)
        self.assertEqual(result["value"], 10.0)
        self.assertEqual(result["position"], "at")
        self.assertIn("not an exchange official", result["method_note"])

    def test_flat_bollinger_and_kdj_have_defined_values(self):
        df = frame(np.full(30, 10.0))
        bollinger = calculate_bollinger_bands(df)
        kdj = calculate_kdj(df)
        self.assertEqual(bollinger["upper"], 10.0)
        self.assertEqual(bollinger["lower"], 10.0)
        self.assertEqual(bollinger["percent_b"], 0.5)
        self.assertEqual(kdj["k"], 50.0)
        self.assertEqual(kdj["d"], 50.0)
        self.assertEqual(kdj["j"], 50.0)

    def test_mfi_handles_one_sided_and_zero_money_flow(self):
        rising = calculate_mfi(frame(np.arange(1.0, 31.0)))
        flat = calculate_mfi(frame(np.full(30, 10.0)))
        self.assertEqual(rising["value"], 100.0)
        self.assertEqual(rising["zone"], "overbought")
        self.assertEqual(flat["value"], 50.0)
        self.assertEqual(flat["zone"], "neutral")

    def test_volume_indicators_fail_explicitly_when_volume_is_missing(self):
        df = frame(np.arange(1.0, 31.0), np.zeros(30))
        self.assertEqual(calculate_obv(df)["status"], "unavailable")
        self.assertEqual(calculate_rolling_vwap(df)["status"], "unavailable")
        self.assertEqual(calculate_mfi(df)["status"], "unavailable")

    def test_bundle_has_all_requested_indicators(self):
        result = calculate_extended_indicators(frame(np.arange(1.0, 31.0)))
        self.assertEqual(set(result), {"obv", "vwap", "bollinger", "kdj", "mfi"})


if __name__ == "__main__":
    unittest.main()
