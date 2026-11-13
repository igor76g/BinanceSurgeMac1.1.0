import unittest

from binance_surge.analyzer import build_rows, candidate_symbols
from binance_surge.binance_api import Candle
from binance_surge.candles import CandleStore
from binance_surge.metrics import compute_stats, natr_values
from binance_surge.monitor import Monitor
from binance_surge.settings import Settings

MIN = 60_000


def candle(i, close=100.0, spread=0.1, volume=1000.0, trades=100):
    """Candle number i (0 = oldest). `spread` = high-low as a share of price in %."""
    half = close * spread / 200.0
    return Candle(
        open_time=i * MIN, close_time=i * MIN + 59_999, open=close,
        high=close + half, low=close - half, close=close, quote_volume=volume, trades=trades,
    )


def quiet_then_surge(n_quiet=195, n_surge=15, surge_volume=5000.0, surge_spread=0.8, surge_trades=900):
    quiet = [candle(i) for i in range(n_quiet)]
    surge = [candle(n_quiet + j, spread=surge_spread, volume=surge_volume, trades=surge_trades) for j in range(n_surge)]
    return quiet + surge


class MetricsTest(unittest.TestCase):
    def test_natr_uses_previous_close(self):
        a = candle(0, close=100.0, spread=0.0)
        b = Candle(MIN, 2 * MIN - 1, 100.0, 102.0, 101.0, 101.5, 1.0, 1)  # gap up: TR = 102 - 100 = 2
        values = natr_values([a, b])
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 2.0 / 101.5 * 100)

    def test_ratios(self):
        st = compute_stats(quiet_then_surge(), window=15, baseline=180)
        self.assertAlmostEqual(st.volume_ratio, 5.0)      # 5000 vs 1000 per candle
        self.assertAlmostEqual(st.natr_ratio, 8.0, places=1)  # 0.8% vs 0.1%
        self.assertEqual(st.trades_window, 900 * 15)
        self.assertAlmostEqual(st.natr_window, 0.8, places=2)

    def test_not_enough_history(self):
        self.assertIsNone(compute_stats([candle(i) for i in range(20)], window=15, baseline=180))


class AnalyzerTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.volumes = {"AAAUSDT": 20e6, "BBBUSDT": 40e6, "CCCUSDT": 5e6, "DDDUSDT": 100e6}

    def test_candidates_use_lowest_volume_limit(self):
        got = candidate_symbols(self.settings, self.volumes, set(self.volumes))
        self.assertEqual(got, ["AAAUSDT", "BBBUSDT", "DDDUSDT"])  # CCC (5M) is below 15M

    def test_surge_and_top_conditions(self):
        strong = compute_stats(quiet_then_surge(), 15, 180)               # x5 volume, x8 NATR, 13500 trades
        weak_vol = compute_stats(quiet_then_surge(surge_volume=3000.0), 15, 180)  # x3 volume: not a surge
        stats = {"AAAUSDT": strong, "BBBUSDT": weak_vol, "DDDUSDT": strong}
        surge, top = build_rows(self.settings, self.volumes, stats)
        self.assertEqual({r.symbol for r in surge}, {"AAAUSDT", "DDDUSDT"})
        # top list needs 24h volume > 30M: AAA (20M) is excluded, BBB (40M) and DDD pass
        self.assertEqual({r.symbol for r in top}, {"BBBUSDT", "DDDUSDT"})

    def test_top_is_sorted_and_limited(self):
        stats, volumes = {}, {}
        for i in range(10):
            sym = f"C{i}USDT"
            volumes[sym] = 50e6
            stats[sym] = compute_stats(quiet_then_surge(surge_trades=700 + 10 * i), 15, 180)
        _, top = build_rows(Settings(), volumes, stats)
        self.assertEqual(len(top), 7)
        self.assertEqual(top[0].symbol, "C9USDT")
        self.assertEqual([r.trades for r in top], sorted((r.trades for r in top), reverse=True))

    def test_min_trades_and_natr_thresholds(self):
        st = compute_stats(quiet_then_surge(surge_trades=600), 15, 180)  # 9000 trades < 10000
        _, top = build_rows(Settings(), {"AAAUSDT": 50e6}, {"AAAUSDT": st})
        self.assertEqual(top, [])
        st = compute_stats(quiet_then_surge(surge_spread=0.5), 15, 180)  # NATR 0.5 < 0.7
        surge, top = build_rows(Settings(), {"AAAUSDT": 50e6}, {"AAAUSDT": st})
        self.assertEqual((surge, top), ([], []))


class FakeApi:
    def __init__(self, series):
        self.series = series          # symbol -> full list of candles
        self.calls = []
        self.now_index = None

    def usdt_perpetual_symbols(self):
        return set(self.series)

    def quote_volumes(self):
        return {s: 50e6 for s in self.series}

    def klines(self, symbol, limit, start_time=None):
        self.calls.append((symbol, limit, start_time))
        candles = [c for c in self.series[symbol] if c.open_time <= self.now_index * MIN]
        if start_time is not None:
            candles = [c for c in candles if c.open_time >= start_time]
        return candles[-limit:]


class StoreAndMonitorTest(unittest.TestCase):
    def test_incremental_loading(self):
        series = {"AAAUSDT": quiet_then_surge(n_quiet=300)}
        api = FakeApi(series)
        store = CandleStore(api, workers=1)
        api.now_index = 250                                   # candle 250 is still forming
        now_ms = 250 * MIN + 30_000
        store.update(["AAAUSDT"], 195, now_ms)
        self.assertEqual(len(store.get("AAAUSDT")), 195)
        self.assertEqual(store.get("AAAUSDT")[-1].open_time, 249 * MIN)
        api.calls.clear()
        api.now_index = 255
        store.update(["AAAUSDT"], 195, 255 * MIN + 30_000)
        self.assertEqual(store.get("AAAUSDT")[-1].open_time, 254 * MIN)
        self.assertEqual(len(store.get("AAAUSDT")), 195)
        self.assertLessEqual(api.calls[0][1], 10)             # only a few new candles were requested
        times = [c.open_time for c in store.get("AAAUSDT")]
        self.assertEqual(times, sorted(set(times)))           # no duplicates

    def test_monitor_cycle_notifies_once(self):
        api = FakeApi({"AAAUSDT": quiet_then_surge(), "BBBUSDT": [candle(i) for i in range(210)]})
        api.now_index = 210
        results, alerts, logs = [], [], []
        monitor = Monitor(
            api, lambda: Settings(),
            on_results=lambda surge, top, since, now: results.append((surge, top)),
            on_alert=lambda row, s: alerts.append(row.symbol),
            log=logs.append, set_status=lambda t: None,
        )
        settings = Settings()
        now = 210 * MIN + 30.0 * 1000 / 1000  # seconds
        monitor.cycle(settings, 210 * 60 + 30)
        surge, top = results[-1]
        self.assertEqual([r.symbol for r in surge], ["AAAUSDT"])
        self.assertEqual([r.symbol for r in top], ["AAAUSDT"])
        self.assertEqual(alerts, ["AAAUSDT"])
        monitor.cycle(settings, 210 * 60 + 330)               # still a surge, but in cooldown
        self.assertEqual(alerts, ["AAAUSDT"])

    def test_cooldown_survives_restart(self):
        import tempfile, pathlib
        path = pathlib.Path(tempfile.mkdtemp()) / "alert_times.json"
        series = {"AAAUSDT": quiet_then_surge(), "BBBUSDT": [candle(i) for i in range(210)]}

        def make(alerts):
            api = FakeApi(series)
            api.now_index = 210
            return Monitor(api, lambda: Settings(), lambda *a: None, lambda row, s: alerts.append(row.symbol),
                           lambda t: None, lambda t: None, state_path=path)

        first, second = [], []
        make(first).cycle(Settings(), 210 * 60 + 30)
        self.assertEqual(first, ["AAAUSDT"])
        make(second).cycle(Settings(), 210 * 60 + 90)         # new Monitor (Stop/Start): still in cooldown
        self.assertEqual(second, [])
        third = []
        make(third).cycle(Settings(), 210 * 60 + 30 + 31 * 60)  # 31 minutes later: allowed again
        self.assertEqual(third, ["AAAUSDT"])

    def test_check_now_does_not_repeat_alerts(self):
        api = FakeApi({"AAAUSDT": quiet_then_surge()})
        api.now_index = 210
        alerts = []
        monitor = Monitor(api, lambda: Settings(), lambda *a: None, lambda row, s: alerts.append(row.symbol),
                          lambda t: None, lambda t: None)
        for k in range(4):                                    # "Check now" pressed several times
            monitor.cycle(Settings(), 210 * 60 + 30 + 10 * k)
        self.assertEqual(alerts, ["AAAUSDT"])

    def test_connection_watchdog(self):
        warnings = []
        monitor = Monitor(FakeApi({}), lambda: Settings(), lambda *a: None, lambda *a: None,
                          lambda t: None, lambda t: None, warn=lambda t, m: warnings.append(t))
        for _ in range(5):
            monitor._cycle_failed("boom")
        self.assertEqual(warnings, ["Connection lost"])
        monitor._cycle_ok()
        self.assertEqual(warnings, ["Connection lost", "Connection restored"])


if __name__ == "__main__":
    unittest.main()
