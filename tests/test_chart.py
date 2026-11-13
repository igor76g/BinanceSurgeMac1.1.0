import tempfile
import unittest
from pathlib import Path

from binance_surge import settings as st
from binance_surge.binance_api import Candle
from binance_surge.chart import MAX_VIEW, MIN_VIEW, build_scene, clamp_offset, clamp_view, visible


def candles(n, start=100.0):
    return [
        Candle(i * 60_000, i * 60_000 + 59_999, start + i, start + i + 2, start + i - 1, start + i + 1, 1000.0, 10)
        for i in range(n)
    ]


class ChartTest(unittest.TestCase):
    def test_view_is_clamped(self):
        self.assertEqual(clamp_view(5, 1000), MIN_VIEW)
        self.assertEqual(clamp_view(5000, 1000), MAX_VIEW)
        self.assertEqual(clamp_view(120, 50), 50)
        self.assertEqual(clamp_view(120, 10), 10)

    def test_offset_is_clamped(self):
        self.assertEqual(clamp_offset(-5, 1000, 100), 0)
        self.assertEqual(clamp_offset(5000, 1000, 100), 900)
        self.assertEqual(clamp_offset(50, 1000, 100), 50)

    def test_visible_slice_follows_the_offset(self):
        data = candles(300)
        self.assertEqual(visible(data, 100, 0)[-1], data[-1])
        self.assertEqual(visible(data, 100, 40)[-1], data[-41])
        self.assertEqual(visible(data, 100, 10 ** 9)[0], data[0])

    def test_scene_fits_the_shown_candles(self):
        data = candles(200)
        new = build_scene(visible(data, 50, 0), data[-1].close, 800, 400)
        old = build_scene(visible(data, 50, 100), data[-1].close, 800, 400)
        self.assertEqual(len(new.candles), 50)
        self.assertGreater(new.low, old.high)
        self.assertIsNone(old.price_y)
        self.assertIsNotNone(new.price_y)
        self.assertEqual(len(new.ticks), 5)

    def test_empty_scene(self):
        self.assertEqual(build_scene([], None, 800, 400).candles, [])


class AlertsLogTest(unittest.TestCase):
    def test_clear_alerts_log(self):
        with tempfile.TemporaryDirectory() as d:
            original = st.data_dir
            st.data_dir = lambda: Path(d)
            try:
                path = st.alerts_log_path()
                path.write_text("ALERT something\n", encoding="utf-8")
                st.clear_alerts_log()
                self.assertEqual(path.read_text(encoding="utf-8"), "")
                st.clear_alerts_log()
                path.unlink()
                st.clear_alerts_log()
            finally:
                st.data_dir = original

    def test_defaults(self):
        self.assertTrue(st.Settings().clear_alerts_log_on_exit)


if __name__ == "__main__":
    unittest.main()
