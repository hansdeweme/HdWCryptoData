# test_ta_charts_keltner.py
# Copyright (c) 2025, 2026 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# part of the HdW_crypto_data Project

import os
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")
# local imports
from examples.ta_charts import TACharts


class KeltnerSignalTests(unittest.TestCase):
    def test_signals_are_full_length_and_placed_on_confirmation_candle(self):
        index = pd.date_range("2026-01-01", periods=4, freq="h")
        prices = pd.Series([10.0, 12.0, 15.0, 13.0], index=index)
        kc_lower = pd.Series([11.0, 8.0, 8.0, 8.0], index=index)
        kc_upper = pd.Series([20.0, 20.0, 14.0, 20.0], index=index)

        chart = object.__new__(TACharts)
        chart.df = pd.DataFrame({
            'close': prices,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'kc_middle': (kc_upper + kc_lower) / 2,
        })
        chart.MARKET = 'BONKUSDT'
        # Exercise the chart's hdw-ta integration without opening a browser.
        with patch.object(chart, '_show_chart') as show_chart:
            chart.plot_keltner(1)
        show_chart.assert_called_once()
        traces = {trace.name: trace for trace in show_chart.call_args.args[0].data}
        buy_trace, sell_trace = traces['Buy Signal'], traces['Sell Signal']
        buy_price = pd.Series(buy_trace.y, index=pd.DatetimeIndex(buy_trace.x))
        sell_price = pd.Series(sell_trace.y, index=pd.DatetimeIndex(sell_trace.x))

        self.assertIsInstance(buy_price, pd.Series)
        self.assertIsInstance(sell_price, pd.Series)
        self.assertTrue(buy_price.index.equals(index))
        self.assertTrue(sell_price.index.equals(index))
        self.assertEqual(len(buy_price), len(prices))
        self.assertEqual(len(sell_price), len(prices))
        self.assertTrue(np.isnan(buy_price.iloc[0]))
        self.assertEqual(buy_price.iloc[1], prices.iloc[1])
        self.assertTrue(np.isnan(sell_price.iloc[2]))
        self.assertEqual(sell_price.iloc[3], prices.iloc[3])
        self.assertEqual(buy_price.notna().tolist(), [False, True, False, False])
        self.assertEqual(sell_price.notna().tolist(), [False, False, False, True])


if __name__ == "__main__":
    unittest.main()
