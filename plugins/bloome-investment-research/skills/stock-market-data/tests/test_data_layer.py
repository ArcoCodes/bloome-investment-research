from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import snapshot_store
from data_contract import attach_contract, leaf_paths, source_record
from provider_runtime import (
    ProviderConfigurationError,
    ProviderUnavailable,
    configured_chain,
    provider_market_metadata,
    route,
)
from provider_runtime import RoutedResult
from security_master import build_security_identity


class ProviderRoutingTests(unittest.TestCase):
    def test_mainland_price_chain_is_market_specific_and_explicit(self):
        self.assertEqual(
            configured_chain("prices", market="CN-SH"),
            ("tencent", "sina", "yfinance"),
        )
        self.assertEqual(configured_chain("prices", market="JP"), ("yfinance",))

    def test_yfinance_japan_and_korea_delay_is_explicit(self):
        for market in ("JP", "KR"):
            metadata = provider_market_metadata("yfinance", "prices", market)
            self.assertEqual(metadata["availability_status"], "delayed")
            self.assertEqual(metadata["declared_delay_seconds"], 1200)

    def test_single_provider_never_silently_falls_back(self):
        config = {
            "fallback_policy": "explicit_only", "dataset_modes": {"prices": "fallback"},
            "datasets": {"prices": ["primary"]},
        }
        attempted = []

        def operation(provider):
            attempted.append(provider)
            raise ProviderUnavailable("down")

        with self.assertRaises(ProviderUnavailable):
            route("prices", operation, config=config)
        self.assertEqual(attempted, ["primary"])

    def test_explicit_fallback_records_attempts(self):
        config = {
            "fallback_policy": "explicit_only",
            "dataset_modes": {"prices": "fallback"},
            "datasets": {"prices": ["primary", "backup"]},
        }

        def operation(provider):
            if provider == "primary":
                raise ProviderUnavailable("down")
            return 42

        result = route("prices", operation, config=config)
        self.assertEqual(result.value, 42)
        self.assertEqual(result.provider, "backup")
        self.assertTrue(result.fallback_used)
        self.assertEqual([a["provider"] for a in result.attempts], ["primary", "backup"])

    def test_unconfigured_dataset_fails_closed(self):
        config = {"fallback_policy": "explicit_only", "dataset_modes": {}, "datasets": {}}
        with self.assertRaises(ProviderConfigurationError):
            configured_chain("prices", config=config)

    def test_union_dataset_cannot_be_sent_through_fallback_router(self):
        config = {
            "fallback_policy": "explicit_only", "dataset_modes": {"news": "union"},
            "datasets": {"news": ["one", "two"]},
        }
        with self.assertRaises(ProviderConfigurationError):
            route("news", lambda provider: provider, config=config)


class IdentityAndContractTests(unittest.TestCase):
    def test_name_change_does_not_change_provisional_identity(self):
        first = build_security_identity("0700.HK", company_name="Tencent")
        second = build_security_identity("0700.HK", company_name="Tencent Holdings")
        self.assertEqual(first["entity"]["entity_id"], second["entity"]["entity_id"])
        self.assertEqual(first["security"]["security_id"], second["security"]["security_id"])
        self.assertEqual(first["listing"]["mic"], "XHKG")
        self.assertEqual(first["entity"]["resolution_status"], "provisional")

    def test_source_maps_every_leaf_field(self):
        payload = {"price": 10, "identity": {"security": {"security_id": "sec_1"}}}
        source = source_record(
            "test", dataset="prices", effective_at="2026-08-20",
            published_at="2026-08-20T08:00:00Z", retrieved_at="2026-08-20T08:01:00Z",
            adjustment="unadjusted", fields=leaf_paths(payload),
        )
        result = attach_contract(payload, sources=[source])
        self.assertEqual(set(source["fields"]), {"price", "identity.security.security_id"})
        self.assertIn("payload_sha256", result["_meta"])


class PointInTimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = snapshot_store.SNAPSHOT_ROOT
        snapshot_store.SNAPSHOT_ROOT = Path(self.temp.name)

    def tearDown(self):
        snapshot_store.SNAPSHOT_ROOT = self.previous_root
        self.temp.cleanup()

    def test_as_of_rejects_future_publication_and_selects_latest_visible(self):
        security_id = "sec_test"
        for price, published, retrieved in [
            (10, "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z"),
            (20, "2026-02-01T12:00:00Z", "2026-02-01T12:01:00Z"),
        ]:
            payload = {"price": price}
            source = source_record(
                "test", dataset="prices", effective_at=published,
                published_at=published, retrieved_at=retrieved, fields=["price"],
            )
            snapshot_store.save_snapshot(
                "prices", security_id, attach_contract(payload, sources=[source])
            )
        result = snapshot_store.query_as_of("prices", security_id, "2026-01-15")
        self.assertEqual(result["price"], 10)
        with self.assertRaises(LookupError):
            snapshot_store.query_as_of("prices", security_id, "2025-12-31")

    def test_missing_publication_time_is_never_historical(self):
        source = source_record(
            "test", dataset="financials", effective_at="2025-12-31",
            published_at=None, retrieved_at="2026-02-01T00:00:00Z", fields=["revenue"],
        )
        snapshot_store.save_snapshot(
            "financials", "sec_test", attach_contract({"revenue": 1}, sources=[source])
        )
        with self.assertRaises(LookupError):
            snapshot_store.query_as_of("financials", "sec_test", "2026-03-01")


class CoreAdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = snapshot_store.SNAPSHOT_ROOT
        snapshot_store.SNAPSHOT_ROOT = Path(self.temp.name)

    def tearDown(self):
        snapshot_store.SNAPSHOT_ROOT = self.previous_root
        self.temp.cleanup()

    def test_price_adapter_emits_identity_provenance_and_snapshot(self):
        import fetch_price

        class FakeFastInfo:
            last_price = 101
            previous_close = 100

        class FakeSeries:
            class _ILoc:
                def __getitem__(self, index):
                    return 1000

            iloc = _ILoc()

        class FakeHistory:
            columns = []

        class FakeTicker:
            info = {
                "shortName": "Example Corp",
                "postMarketPrice": 103,
                "postMarketTime": 1787274000,
            }

        routed = RoutedResult(
            value={
                "kind": "yfinance",
                "ticker": FakeTicker(),
                "fast_info": FakeFastInfo(),
                "history": FakeHistory(),
                "history_metadata": {"regularMarketTime": 1787270400},
            },
            provider="yfinance", attempts=({"provider": "yfinance", "status": "success"},),
            fallback_used=False,
        )
        with patch.object(fetch_price, "route", return_value=routed):
            result = fetch_price.fetch_price("EXM")
        self.assertEqual(result["data_status"], "research_grade_current_or_delayed")
        self.assertEqual(result["identity"]["listing"]["currency"], "USD")
        self.assertEqual(result["_meta"]["sources"][0]["provider"], "yfinance")
        self.assertFalse(result["_meta"]["fallback_used"])
        self.assertEqual(result["post_market_price"], 103.0)
        self.assertEqual(result["post_market_change_pct"], 1.98)
        self.assertEqual(result["post_market_basis"], "latest_regular_session_price")
        self.assertEqual(result["post_market_basis_price"], 101.0)
        self.assertEqual(result["post_market_provider"], "yfinance")
        self.assertIsNotNone(result["post_market_time"])
        self.assertNotEqual(result["data_time"], result["_meta"]["retrieved_at"])
        self.assertEqual(
            result["_meta"]["sources"][0]["effective_at"], result["data_time"]
        )
        self.assertEqual(
            result["_meta"]["sources"][0]["published_at_basis"], "first_observed"
        )
        security_id = result["identity"]["security"]["security_id"]
        self.assertTrue(list((snapshot_store.SNAPSHOT_ROOT / "prices" / security_id).glob("*.json")))

    def test_mainland_quote_uses_near_real_time_unofficial_contract(self):
        import fetch_price

        routed = RoutedResult(
            value={
                "kind": "normalized_quote",
                "price": 10.5,
                "prev_close": 10.0,
                "volume": 123400,
                "name": "\u793a\u4f8b\u80a1\u4efd",
                "quote_time": "2026-08-21T06:30:00Z",
                "market_timestamp_source": "tencent_quote_field_30",
                "volume_basis": "reported_lots_x100",
                "source_url": "https://qt.gtimg.cn/q=sh600000",
            },
            provider="tencent",
            attempts=({"provider": "tencent", "status": "success"},),
            fallback_used=False,
        )
        with patch.object(fetch_price, "route", return_value=routed) as mocked_route:
            result = fetch_price.fetch_price("600000.SS")
        self.assertEqual(result["data_status"], "research_grade_near_real_time_unofficial")
        self.assertEqual(result["identity"]["listing"]["market"], "CN-SH")
        self.assertEqual(result["quote_availability"]["service_level"], "none")
        self.assertIsNone(result["quote_availability"]["declared_delay_seconds"])
        self.assertEqual(result["_meta"]["sources"][0]["provider"], "tencent")
        self.assertEqual(mocked_route.call_args.kwargs["market"], "CN-SH")

    def test_mainland_quote_parsers_preserve_provider_timestamp(self):
        import fetch_price

        tencent = (
            'v_sh600000="1~\u793a\u4f8b\u80a1\u4efd~600000~10.50~10.00~10.10~1234~0~0~0~'
            '0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~20260821143015~";'
        )
        sina_fields = ["\u793a\u4f8b\u80a1\u4efd", "10.10", "10.00", "10.50", "10.60", "9.90", "10.49", "10.50", "123400", "1295700"]
        sina_fields.extend(["0"] * 20)
        sina_fields.extend(["2026-08-21", "14:30:16", "00"])
        sina = f'var hq_str_sh600000="{",".join(sina_fields)}";'
        tq = fetch_price._parse_tencent_quote(tencent, "https://qt.gtimg.cn/q=sh600000")
        sq = fetch_price._parse_sina_quote(sina, "https://hq.sinajs.cn/list=sh600000")
        self.assertEqual(tq["quote_time"], "2026-08-21T06:30:15Z")
        self.assertEqual(tq["volume"], 123400)
        self.assertEqual(sq["quote_time"], "2026-08-21T06:30:16Z")
        self.assertEqual(sq["volume"], 123400)

    def test_mainland_network_error_becomes_fallback_signal(self):
        import fetch_price

        with patch.object(fetch_price, "_request_gb18030", side_effect=OSError("down")):
            with self.assertRaises(ProviderUnavailable):
                fetch_price._fetch_price_raw("tencent", "600000.SS", "CN-SH")


class DispatcherAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_root = snapshot_store.SNAPSHOT_ROOT
        snapshot_store.SNAPSHOT_ROOT = Path(self.temp.name)

    def tearDown(self):
        snapshot_store.SNAPSHOT_ROOT = self.previous_root
        self.temp.cleanup()

    def test_events_union_records_observed_and_missing_providers(self):
        import dispatch_adapters

        fake = types.SimpleNamespace(fetch_events=lambda *args, **kwargs: {
            "symbol": "AAPL",
            "events": [{
                "type": "filing", "date": "2026-08-20", "source": "SEC EDGAR",
                "url": "https://www.sec.gov/test", "title": "8-K",
            }],
            "has_actionable": True,
            "has_hard_event": True,
        })
        with patch.dict(sys.modules, {"fetch_events": fake}):
            result = dispatch_adapters.events("AAPL")
        attempts = {row["provider"]: row["status"] for row in result["_meta"]["provider_attempts"]}
        self.assertEqual(attempts["sec_edgar"], "success")
        self.assertEqual(attempts["yfinance"], "not_observed")
        self.assertIn("identity", result)
        self.assertTrue(any("events[0].title" in source["fields"] for source in result["_meta"]["sources"]))

    def test_earnings_adapter_preserves_explicit_fallback_audit(self):
        import dispatch_adapters

        fake = types.SimpleNamespace(fetch_earnings_calendar=lambda symbol: {
            "symbol": symbol,
            "next_earnings_date": "2026-10-01",
            "source": "hkex_disclosure",
            "checked_at": "2026-08-22T00:00:00Z",
            "_routing": {
                "provider": "hkex", "fallback_used": True,
                "attempts": [
                    {"provider": "yfinance", "status": "unavailable", "reason": "no data"},
                    {"provider": "hkex", "status": "success"},
                ],
                "source_url": "https://www1.hkexnews.hk/",
            },
        })
        with patch.dict(sys.modules, {"fetch_earnings_calendar": fake}):
            result = dispatch_adapters.earnings_calendar("0700.HK")
        self.assertTrue(result["_meta"]["fallback_used"])
        self.assertEqual(result["_meta"]["provider_attempts"][1]["provider"], "hkex")

    def test_technicals_uses_frame_provenance(self):
        import dispatch_adapters

        fake = types.SimpleNamespace(analyze=lambda symbol, period: {
            "symbol": symbol,
            "as_of": "2026-08-21T16:00:00Z",
            "trend": "up",
            "data_provenance": {
                "daily": {
                    "source": "Yahoo Finance via yfinance",
                    "source_url": "https://finance.yahoo.com/",
                    "adjustment": "auto_adjust=True",
                    "last_bar": "2026-08-21T16:00:00Z",
                }
            },
        })
        with patch.dict(sys.modules, {"analyze_technicals": fake}):
            result = dispatch_adapters.technicals("AAPL")
        self.assertEqual(result["_meta"]["sources"][0]["provider"], "yfinance")
        self.assertEqual(result["_meta"]["sources"][0]["adjustment"], "auto_adjust=True")


if __name__ == "__main__":
    unittest.main()
