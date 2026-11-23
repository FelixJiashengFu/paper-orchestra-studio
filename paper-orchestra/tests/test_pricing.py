"""CAD cost estimate (utils/pricing.py) and the finer usage counters it reads.

Offline: online refresh is off or its fetcher is mocked, and the price cache
lives in a temporary folder.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import pricing as P  # noqa: E402
from utils import usage_tracker as U  # noqa: E402


class _Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"PO_PRICING_CACHE_DIR": self.tmp.name, "PO_PRICING_ONLINE": "0"})
        self.env.start()
        P._cache = None

    def tearDown(self):
        P._cache = None
        self.env.stop()
        self.tmp.cleanup()

    def write_cache(self, data):
        with open(os.path.join(self.tmp.name, "pricing.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)
        P._cache = None


class LookupTests(_Isolated):
    def test_builtin_official_prices(self):
        price, kind = P.lookup("anthropic", "claude-sonnet-5-5")
        self.assertEqual((kind, price.currency, price.input, price.output), ("official", "USD", 2, 10))
        price, kind = P.lookup("longcat", "LongCat-2.5")  # case-insensitive
        self.assertEqual((kind, price.currency), ("official", "CNY"))
        self.assertEqual(P.lookup("anthropic", "claude-sonnet-4-5-20250929")[0].input, 3)  # dated id
        self.assertEqual(P.lookup("gemini", "models/gemini-3-flash-preview")[0].input, 0.5)
        self.assertIsNone(P.lookup("ollama", "llama-local"))
        self.assertIsNone(P.lookup("", ""))

    def test_dated_price_changes(self):
        entry = P.BUILTIN_PRICES["gemini"]["gemini-3.8-flash"]
        self.assertEqual(P._dated(entry, "2026-12-31").input, 0.75)
        self.assertEqual(P._dated(entry, "2027-01-01").input, 1.50)
        argon = P.BUILTIN_PRICES["gemini"]["gemini-4-argon"]
        self.assertEqual(P._dated(argon, "2026-11-30").output, 10)
        self.assertEqual(P._dated(argon, "2026-12-01").output, 20)

    def test_relay_serving_a_known_model_uses_the_makers_price(self):
        price, kind = P.lookup("myrelay", "claude-opus-5-5")
        self.assertEqual((kind, price.input), ("official:anthropic", 4))
        price, kind = P.lookup("openrouter", "anthropic/claude-opus-5-5")  # no feed cached
        self.assertEqual(kind, "official:anthropic")

    def test_cached_feeds(self):
        now = time.time()
        self.write_cache({
            "litellm": {"fetched": now, "value": {"openai/gpt-9": {"input": 3.0, "output": 9.0}}},
            "openrouter": {"fetched": now, "value": {"vendor/model-x": {"input": 1.0, "output": 2.0,
                                                                        "search_per_1k": 4.0}}},
        })
        price, kind = P.lookup("openai", "gpt-9")
        self.assertEqual((kind, price.input, price.output), ("litellm", 3.0, 9.0))
        price, kind = P.lookup("openrouter", "vendor/model-x")
        self.assertEqual((kind, price.search_per_1k), ("openrouter", 4.0))
        self.assertEqual(P.lookup("openai", "gpt-4o")[1], "official")  # built-in wins

    def test_exact_feed_id_before_a_shortened_builtin_one(self):
        self.write_cache({"litellm": {"fetched": time.time(), "value": {
            "anthropic/claude-sonnet-4-5-20250929": {"input": 9.0, "output": 9.0}}}})
        price, kind = P.lookup("anthropic", "claude-sonnet-4-5-20250929")
        self.assertEqual((kind, price.input), ("litellm", 9.0))

    def test_another_relays_price_is_not_used(self):
        self.assertIsNone(P.lookup("myrelay", "deepseek-ai/deepseek-v3"))  # only siliconflow lists it
        self.assertEqual(P.lookup("siliconflow", "deepseek-ai/DeepSeek-V3")[1], "official")

    def test_damaged_cache_never_raises(self):
        self.write_cache({"fx": "x", "litellm": ["bad"], "openrouter": {"fetched": "soon", "value": 3}})
        rows = [{"provider": "anthropic", "model": "claude-sonnet-5-5", "input_tokens": 10, "output_tokens": 1}]
        rep = P.price_report(rows)
        self.assertEqual(rep["fx"]["source"], "builtin")
        self.assertIn("cost", rows[0])
        self.assertIsNone(P.lookup("openai", "gpt-unknown"))

        def broken(url):
            raise OSError("offline")

        with mock.patch.dict(os.environ, {"PO_PRICING_ONLINE": "1"}), mock.patch.object(P, "_get_json", broken):
            P.refresh()  # a damaged "fetched" counts as stale: no exception

    def test_cache_writes_use_their_own_temp_file(self):
        P._save_cache({"fx": {"fetched": 1, "value": {"rates": {"USD": 1.4}}}})
        P._save_cache({"fx": {"fetched": 2, "value": {"rates": {"USD": 1.5}}}})
        self.assertEqual(os.listdir(self.tmp.name), ["pricing.json"])  # no temp files left behind
        P._cache = None
        self.assertEqual(P.fx_rates()["rates"]["USD"], 1.5)

    def test_long_context_threshold(self):
        self.assertEqual(P.long_context_threshold("anthropic", "claude-haiku-5-5"), 100_000)
        self.assertIsNone(P.long_context_threshold("anthropic", "claude-sonnet-5-5"))
        self.assertIsNone(P.long_context_threshold("nowhere", "nothing"))


class CostTests(_Isolated):
    def cost(self, provider, model, **row):
        return P.cost_of(row, P.lookup(provider, model)[0])

    def test_plain_tokens(self):
        self.assertAlmostEqual(self.cost("anthropic", "claude-sonnet-5-5", input_tokens=1_000_000,
                                         output_tokens=1_000_000), 12.0)

    def test_cache_reads_and_writes(self):
        # 0.5M uncached at $2, 0.4M read at $0.10, 0.1M written (5 min) at $2.50, of which none 1h.
        c = self.cost("anthropic", "claude-sonnet-5-5", input_tokens=1_000_000, cache_read_tokens=400_000,
                      cache_write_tokens=100_000)
        self.assertAlmostEqual(c, 0.5 * 2 + 0.4 * 0.10 + 0.1 * 2.5)
        c1h = self.cost("anthropic", "claude-sonnet-5-5", input_tokens=100_000, cache_write_tokens=100_000,
                        cache_write_1h_tokens=100_000)
        self.assertAlmostEqual(c1h, 0.1 * 4)
        # Inconsistent counters never make a negative or larger-than-input share.
        odd = self.cost("anthropic", "claude-sonnet-5-5", input_tokens=10, cache_read_tokens=99)
        self.assertGreaterEqual(odd, 0)

    def test_long_context_tier(self):
        # Haiku 5.5: one 200k-token prompt (over 100k) is billed at the long rates.
        c = self.cost("anthropic", "claude-haiku-5-5", input_tokens=250_000, output_tokens=2_000,
                      long_calls=1, long_input_tokens=200_000, long_output_tokens=1_000)
        expected = (50_000 * 0.10 + 200_000 * 0.50 + 1_000 * 0.50 + 1_000 * 2.50) / 1e6
        self.assertAlmostEqual(c, expected)

    def test_search_and_images(self):
        self.assertAlmostEqual(self.cost("gemini", "gemini-4-hydrogen", search_queries=10), 0.14)
        self.assertAlmostEqual(self.cost("deepseek", "deepseek-flash", search_queries=10), 0.0)
        img = self.cost("gemini", "gemini-3-pro-image", input_tokens=100, output_tokens=1_220,
                        image_output_tokens=1_120)
        self.assertAlmostEqual(img, (100 * 2 + 100 * 12 + 1_120 * 120) / 1e6)
        self.assertAlmostEqual(self.cost("openai", "gpt-image-1", images=2), 0.084)  # no token usage

    def test_price_report_in_cad(self):
        rows = [
            {"provider": "anthropic", "model": "claude-sonnet-5-5", "calls": 1, "errors": 0,
             "input_tokens": 1_000_000, "output_tokens": 0},
            {"provider": "qwen", "model": "qwen4-max", "calls": 1, "errors": 0,
             "input_tokens": 1_000_000, "output_tokens": 0},
            {"provider": "ollama", "model": "llama", "calls": 1, "errors": 0,
             "input_tokens": 5, "output_tokens": 5},
            {"provider": "anthropic", "model": "claude-opus-5-5", "calls": 1, "errors": 1,
             "input_tokens": 0, "output_tokens": 0},
        ]
        rep = P.price_report(rows)
        usd, cny = P.BUILTIN_FX["USD"], P.BUILTIN_FX["CNY"]
        self.assertAlmostEqual(rows[0]["cost"]["cad"], 2 * usd, places=5)
        self.assertEqual(rows[1]["cost"]["currency"], "CNY")
        self.assertAlmostEqual(rows[1]["cost"]["cad"], 10 * cny, places=5)
        self.assertNotIn("cost", rows[2])
        self.assertNotIn("cost", rows[3])  # a failed call costs nothing
        self.assertEqual(rep["currency"], "CAD")
        self.assertAlmostEqual(rep["total"], 2 * usd + 10 * cny, places=5)
        self.assertEqual(rep["by_currency"], {"USD": 2.0, "CNY": 10.0})
        self.assertEqual(rep["unpriced_models"], ["ollama:llama"])
        self.assertEqual(rep["fx"]["source"], "builtin")
        self.assertEqual(rep["prices"]["sources"], ["official"])
        json.dumps(rep)

    def test_bank_of_canada_rates_from_cache(self):
        self.write_cache({"fx": {"fetched": time.time(), "value": {"rates": {"CAD": 1.0, "USD": 1.5, "CNY": 0.2},
                                                                   "date": "2026-11-19"}}})
        rows = [{"provider": "anthropic", "model": "claude-sonnet-5-5", "input_tokens": 1_000_000,
                 "output_tokens": 0}]
        rep = P.price_report(rows)
        self.assertEqual((rep["fx"]["source"], rep["fx"]["date"]), ("bank_of_canada", "2026-11-19"))
        self.assertAlmostEqual(rep["total"], 3.0)


class RefreshTests(_Isolated):
    BOC = {"observations": [{"d": "2026-11-19", "FXUSDCAD": {"v": "1.4400"}, "FXCNYCAD": {"v": "0.2150"},
                             "FXEURCAD": {"v": "bad"}}]}
    LITELLM = {
        "sample_spec": {"input_cost_per_token": 1},
        "gpt-9": {"litellm_provider": "openai", "mode": "chat", "input_cost_per_token": 3e-6,
                  "output_cost_per_token": 9e-6, "cache_read_input_token_cost": 3e-7,
                  "input_cost_per_token_above_272k_tokens": 6e-6, "output_cost_per_token_above_272k_tokens": 1.2e-5},
        "gemini/gemini-9": {"litellm_provider": "gemini", "mode": "chat", "input_cost_per_token": 1e-6,
                            "output_cost_per_token": 2e-6},
        "embed-x": {"litellm_provider": "openai", "mode": "embedding", "input_cost_per_token": 1e-7},
        "bedrock/x": {"litellm_provider": "bedrock", "mode": "chat", "input_cost_per_token": 1e-6},
    }
    OPENROUTER = {"data": [{"id": "Vendor/Model-X", "pricing": {"prompt": "0.000001", "completion": "0.000002",
                                                                "web_search": "0.004"}},
                           {"id": "free/x", "pricing": {"prompt": "0", "completion": "0"}},
                           {"id": "bad/x", "pricing": {"prompt": "-1"}}]}

    def fake_get(self, url):
        self.calls.append(url)
        if "bankofcanada" in url:
            return self.BOC
        if "litellm" in url:
            return self.LITELLM
        return self.OPENROUTER

    def test_refresh_parses_and_caches(self):
        self.calls = []
        with mock.patch.dict(os.environ, {"PO_PRICING_ONLINE": "1"}), mock.patch.object(P, "_get_json", self.fake_get):
            P.refresh()
            self.assertEqual(len(self.calls), 3)
            P.refresh()  # fresh cache: nothing fetched again
            self.assertEqual(len(self.calls), 3)
        P._cache = None  # read back from disk
        fx = P.fx_rates()
        self.assertEqual((fx["source"], fx["rates"]["USD"], fx["rates"]["EUR"]), ("bank_of_canada", 1.44, 1.5717))
        price, kind = P.lookup("openai", "gpt-9")
        self.assertEqual((kind, price.input, price.cache_read, price.long_threshold, price.long_input),
                         ("litellm", 3.0, 0.3, 272_000, 6.0))
        self.assertEqual(P.lookup("gemini", "gemini-9")[0].output, 2.0)
        self.assertIsNone(P.lookup("openai", "embed-x"))
        price, kind = P.lookup("openrouter", "vendor/model-x")
        self.assertEqual((kind, price.input, price.search_per_1k), ("openrouter", 1.0, 4.0))
        self.assertEqual(P.lookup("openrouter", "free/x")[0].input, 0.0)

    def test_failing_source_keeps_the_old_value(self):
        self.write_cache({"fx": {"fetched": 0, "value": {"rates": {"USD": 1.3}, "date": "2026-01-01"}}})

        def broken(url):
            raise OSError("offline")

        with mock.patch.dict(os.environ, {"PO_PRICING_ONLINE": "1"}), mock.patch.object(P, "_get_json", broken):
            P.refresh()
        self.assertEqual(P.fx_rates()["rates"]["USD"], 1.3)

    def test_offline_switch(self):
        self.calls = []
        with mock.patch.object(P, "_get_json", self.fake_get):
            P.refresh(force=True)  # PO_PRICING_ONLINE=0 from setUp
        self.assertEqual(self.calls, [])
        self.assertEqual(P.fx_rates()["source"], "builtin")


class UsageDetailTests(_Isolated):
    def setUp(self):
        super().setUp()
        U.end_run()
        U.reset()

    def tearDown(self):
        U.end_run()
        U.reset()
        super().tearDown()

    def test_extract_details(self):
        gemini = SimpleNamespace(
            usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=1200,
                                           cached_content_token_count=40,
                                           candidates_tokens_details=[
                                               SimpleNamespace(modality="TEXT", token_count=80),
                                               SimpleNamespace(modality="MediaModality.IMAGE", token_count=1120)]),
            candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(web_search_queries=["a", "b"]))])
        self.assertEqual(U.extract_details(gemini), {"cache_read_tokens": 40, "image_output_tokens": 1120,
                                                     "search_queries": 2})
        openai = {"usage": {"prompt_tokens": 50, "completion_tokens": 5,
                            "prompt_tokens_details": {"cached_tokens": 30}}}
        self.assertEqual(U.extract_details(openai), {"cache_read_tokens": 30})
        deepseek = {"usage": {"prompt_tokens": 50, "completion_tokens": 5, "prompt_cache_hit_tokens": 20}}
        self.assertEqual(U.extract_details(deepseek), {"cache_read_tokens": 20})
        claude = SimpleNamespace(usage=SimpleNamespace(input_tokens=5, output_tokens=3, cache_read_input_tokens=7,
                                                       cache_creation_input_tokens=4,
                                                       cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=4)))
        self.assertEqual(U.extract_details(claude), {"cache_read_tokens": 7, "cache_write_tokens": 4,
                                                     "cache_write_1h_tokens": 4})
        images = {"data": [{"b64_json": "x"}, {"url": "u"}]}
        self.assertEqual(U.extract_details(images), {"images": 2})
        self.assertEqual(U.extract_details(None), {})
        self.assertEqual(U.extract_details(object()), {})

    def test_report_carries_details_and_cost(self):
        U.record("anthropic", "claude-haiku-5-5", 150_000, 100, details={"cache_read_tokens": 10, "bogus": 5})
        U.record("anthropic", "claude-haiku-5-5", 1_000, 10)
        rep = U.report()
        row = rep["by_model"][0]
        self.assertEqual((row["long_calls"], row["long_input_tokens"], row["long_output_tokens"]), (1, 150_000, 100))
        self.assertEqual(row["cache_read_tokens"], 10)
        self.assertNotIn("bogus", row)
        self.assertEqual(row["cost"]["currency"], "USD")
        self.assertGreater(rep["cost"]["total"], 0)
        self.assertEqual(set(rep["totals"]), {"calls", "errors", "input_tokens", "output_tokens"})

    def test_each_call_is_priced_in_its_own_tier(self):
        # Haiku 5.5 (long tier above 100k): a long uncached call and a short
        # call read from the cache. Priced one by one, each lands in its tier.
        U.record("anthropic", "claude-haiku-5-5", 150_000, 0)
        U.record("anthropic", "claude-haiku-5-5", 50_000, 0, details={"cache_read_tokens": 50_000})
        amount = U.report()["by_model"][0]["cost"]["amount"]
        self.assertAlmostEqual(amount, (150_000 * 0.50 + 50_000 * 0.01) / 1e6)

    def test_calls_keep_the_price_of_their_own_day(self):
        with mock.patch.object(P, "_today", return_value="2026-11-30"):
            U.record("gemini", "gemini-4-argon", 1_000_000, 0)
        with mock.patch.object(P, "_today", return_value="2026-12-01"):
            U.record("gemini", "gemini-4-argon", 1_000_000, 0)
            row = U.report()["by_model"][0]
        self.assertAlmostEqual(row["cost"]["amount"], 2.0 + 4.0)

    def test_a_call_without_a_price_prices_the_row_from_its_counters(self):
        U.record("openai", "gpt-9", 1_000_000, 0)  # no price known yet
        self.write_cache({"litellm": {"fetched": time.time(), "value": {"openai/gpt-9": {"input": 3.0, "output": 9.0}}}})
        U.record("openai", "gpt-9", 1_000_000, 0)
        row = U.report()["by_model"][0]
        self.assertEqual(row["cost"]["source"], "litellm")
        self.assertAlmostEqual(row["cost"]["amount"], 6.0)
        self.assertFalse([k for k in row if k.startswith("_")])

    def test_images_endpoint_output_is_image_tokens(self):
        rsp = {"data": [{"b64_json": "x"}], "usage": {"input_tokens": 50, "output_tokens": 1120}}
        self.assertEqual(U.extract_details(rsp), {"images": 1, "image_output_tokens": 1120})
        rsp = {"data": [{"b64_json": "x"}], "usage": {"input_tokens": 50, "output_tokens": 1200,
                                                      "output_tokens_details": {"image_tokens": 1100}}}
        self.assertEqual(U.extract_details(rsp)["image_output_tokens"], 1100)
        U.record_response("myrelay", "gemini-3-pro-image",
                          {"data": [{"b64_json": "x"}], "usage": {"input_tokens": 50, "output_tokens": 1120}})
        self.assertAlmostEqual(U.report()["by_model"][0]["cost"]["amount"], (50 * 2 + 1120 * 120) / 1e6)

    def test_pricing_failure_never_breaks_the_report(self):
        U.record("anthropic", "claude-sonnet-5-5", 10, 10)
        with mock.patch.object(P, "price_report", side_effect=RuntimeError("boom")), \
                mock.patch.object(sys, "stderr"):
            rep = U.report()
        self.assertNotIn("cost", rep)
        self.assertNotIn("cost", rep["by_model"][0])

    def test_begin_run_starts_a_background_refresh(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P, "refresh_in_background") as bg:
            U.begin_run(d)
            U.end_run(d)
        bg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
