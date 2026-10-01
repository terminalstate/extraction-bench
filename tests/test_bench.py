import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench  # noqa: E402
from fake_api import FakeAPI, anthropic_ok, deepseek_ok, openai_ok  # noqa: E402

GOOD = {"parties": ["NIKE, Inc.", "Eric Dean Sprunk"], "effective_date": "2001-04-18",
        "jurisdiction": "State of Oregon", "term": {"number": 2, "unit": "years"}}
KEYS = ["effective_date", "jurisdiction", "party", "term"]
DOC = {"id": "doc1", "keys": KEYS, "text": "agreement text", "gold": set()}
PRICE = {"openai": {"provider": "openai", "input": 0.10, "cached_input": 0.01, "output": 0.50},
         "deepseek": {"provider": "deepseek", "input": 0.30, "cached_input": 0.006, "output": 1.20,
                      "offpeak_factor": 0.5},
         "anthropic": {"provider": "anthropic", "input": 1.0, "cached_input": 0.1, "output": 5.0}}


def cfg(**kw):
    base = dict(model="m", split="dev-0", tag="", timeout=0.5, max_attempts=4, output_tries=2,
                max_output_tokens=1000, reasoning_effort="low", backoff_base=0.01, backoff_cap=0.05,
                max_failed_docs=5, max_usd=0, workers=1, limit=0, text_col="text_best")
    base.update(kw)
    return argparse.Namespace(**base)


class FakeAPITestCase(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        os.environ.update({"OPENAI_BASE_URL": self.api.url, "DEEPSEEK_BASE_URL": self.api.url,
                           "ANTHROPIC_BASE_URL": self.api.url, "OPENAI_API_KEY": "k",
                           "DEEPSEEK_API_KEY": "k", "ANTHROPIC_API_KEY": "k"})
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.saved_runs = bench.RUNS

    def tearDown(self):
        bench.RUNS = self.saved_runs
        self.api.close()
        self.tmp.cleanup()

    def process(self, provider, c=None, state=None, doc=DOC):
        c = c or cfg()
        p = bench.PROVIDERS[provider]("m", c)
        return bench.process_doc(doc, p, PRICE[provider], c, state or bench.RunState(0), self.out)


class ProviderTests(FakeAPITestCase):
    def test_openai_ok_labels_cost_and_request(self):
        self.api.queue["/chat/completions"] = [openai_ok(GOOD)]
        r = self.process("openai")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(set(map(tuple, r["pred"])), {
            ("party", "NIKE_INC."), ("party", "ERIC_DEAN_SPRUNK"), ("effective_date", "2001-04-18"),
            ("jurisdiction", "OREGON"), ("term", "2_YEARS")})
        self.assertAlmostEqual(r["cost_usd"], (800 * 0.10 + 200 * 0.01 + 100 * 0.50) / 1e6)
        _, headers, body = self.api.requests[0]
        self.assertEqual(body["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(headers.get("Authorization") or headers.get("authorization"), "Bearer k")

    def test_429_with_retry_after_then_ok(self):
        self.api.queue["/chat/completions"] = [
            {"status": 429, "headers": {"retry-after": "0"}, "body": {"error": "rate"}}, openai_ok(GOOD)]
        r = self.process("openai")
        self.assertEqual(r["status"], "ok")
        self.assertEqual([a["status"] for a in r["attempts"]], [429, 200])

    def test_500_timeout_disconnect_then_ok(self):
        self.api.queue["/chat/completions"] = [{"status": 500, "body": {}}, {"delay": 1.5, **openai_ok(GOOD)},
                                               {"close": True}, openai_ok(GOOD)]
        r = self.process("openai")
        self.assertEqual(r["status"], "ok")
        self.assertEqual([a["kind"] for a in r["attempts"]], ["http", "timeout", "network", "http"])

    def test_retries_exhausted_marks_doc_failed(self):
        self.api.queue["/chat/completions"] = [{"status": 503, "body": {}}] * 5
        r = self.process("openai", cfg(max_attempts=3))
        self.assertEqual(r["status"], "failed")
        self.assertEqual(len(r["attempts"]), 3)
        self.assertEqual(r["pred"], [])

    def test_400_is_not_retried(self):
        self.api.queue["/chat/completions"] = [{"status": 400, "body": {"error": "context too long"}}]
        r = self.process("openai")
        self.assertEqual(r["status"], "failed")
        self.assertEqual(len(r["attempts"]), 1)

    def test_401_stops_the_run(self):
        self.api.queue["/v1/messages"] = [{"status": 401, "body": {"error": "bad key"}}]
        with self.assertRaises(bench.Fatal):
            self.process("anthropic")

    def test_invalid_json_then_ok(self):
        self.api.queue["/chat/completions"] = [deepseek_ok(content='{"parties": ["A"'), deepseek_ok(GOOD)]
        r = self.process("deepseek")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["invalid_outputs"], 1)
        self.assertTrue(r["outputs"][0]["problems"][0].startswith("invalid json"))

    def test_empty_content_twice_is_invalid(self):
        self.api.queue["/chat/completions"] = [deepseek_ok(content=""), deepseek_ok(content="")]
        r = self.process("deepseek")
        self.assertEqual(r["status"], "invalid")
        self.assertEqual(r["pred"], [])
        self.assertEqual(r["invalid_outputs"], 2)

    def test_code_fence_is_repaired(self):
        self.api.queue["/chat/completions"] = [deepseek_ok(content="```json\n" + json.dumps(GOOD) + "\n```")]
        r = self.process("deepseek")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["outputs"][0]["repairs"], ["code fence stripped"])

    def test_schema_violation_then_ok(self):
        bad = dict(GOOD, parties="NIKE", effective_date="18/04/2001")
        self.api.queue["/chat/completions"] = [openai_ok(bad), openai_ok(GOOD)]
        r = self.process("openai")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["outputs"][0]["problems"]), 2)

    def test_anthropic_truncated_then_ok(self):
        self.api.queue["/v1/messages"] = [anthropic_ok({"parties": []}, stop_reason="max_tokens"),
                                          anthropic_ok(GOOD)]
        r = self.process("anthropic")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["outputs"][0]["problems"], ["truncated"])
        self.assertAlmostEqual(r["cost_usd"], 2 * (1000 * 1.0 + 80 * 5.0) / 1e6)
        _, headers, body = self.api.requests[0]
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": "record_fields"})

    def test_budget_stops_further_calls(self):
        state = bench.RunState(max_usd=1e-9)
        self.api.queue["/chat/completions"] = [openai_ok(GOOD), openai_ok(GOOD)]
        self.process("openai", state=state)
        self.assertTrue(state.stop_reason.startswith("budget"))
        r2 = self.process("openai", state=state, doc=dict(DOC, id="doc2"))
        self.assertEqual(r2["attempts"], [])
        self.assertEqual(len(self.api.requests), 1)

    def test_resume_skips_finished_docs(self):
        self.api.queue["/chat/completions"] = [openai_ok(GOOD)]
        self.process("openai")
        r = self.process("openai")
        self.assertTrue(r.get("cached"))
        self.assertEqual(len(self.api.requests), 1)


class RunTests(FakeAPITestCase):
    def test_run_model_end_to_end(self):
        bench.RUNS = self.out / "runs"
        docs = [dict(DOC, id=f"d{i}", gold={("jurisdiction", "OREGON")}) for i in range(5)]
        self.api.queue["/chat/completions"] = [openai_ok(GOOD) for _ in range(5)]
        s = bench.run_model(cfg(model="gpt-6-luna", workers=3, max_usd=1.0), docs)
        self.assertEqual((s["processed"], s["ok"]), (5, 5))
        self.assertAlmostEqual(s["field_f1"]["jurisdiction"], 1.0)
        self.assertTrue((bench.RUNS / "gpt-6-luna__v1__dev-0" / "meta.json").exists())

    def test_run_model_bad_key_raises(self):
        bench.RUNS = self.out / "runs"
        self.api.queue["/chat/completions"] = [{"status": 401, "body": {}} for _ in range(5)]
        with self.assertRaises(bench.Fatal):
            bench.run_model(cfg(model="gpt-6-luna", workers=2), [dict(DOC, id=f"d{i}") for i in range(5)])

    def test_estimate_over_budget_refuses(self):
        bench.RUNS = self.out / "runs"
        with self.assertRaises(bench.Fatal):
            bench.run_model(cfg(model="gpt-6-sol", max_usd=1e-6), [dict(DOC, text="x" * 100000)])
        self.assertEqual(self.api.requests, [])


class NormalisationTests(unittest.TestCase):
    def test_labels(self):
        obj = {"parties": ["Leonard Green & Partners, LP", "  "], "effective_date": None,
               "jurisdiction": "the Commonwealth of Massachusetts", "term": {"number": 1, "unit": "years"}}
        self.assertEqual(bench.to_labels(obj, KEYS), {("party", "LEONARD_GREEN_AND_PARTNERS_LP"),
                                                      ("jurisdiction", "MASSACHUSETTS"), ("term", "1_YEAR")})
        self.assertEqual(bench.to_labels(dict(obj, term={"number": 24, "unit": "months"}), ["term"]),
                         {("term", "24_MONTHS")})
        self.assertEqual(bench.to_labels(dict(obj, term={"number": 55.5, "unit": "months"}), ["term"]),
                         {("term", "55.5_MONTHS")})
        self.assertEqual(bench.to_labels(obj, ["jurisdiction"]), {("jurisdiction", "MASSACHUSETTS")})

    def test_validate(self):
        self.assertEqual(bench.validate(GOOD), [])
        self.assertTrue(bench.validate(dict(GOOD, effective_date="2014-02-30")))
        self.assertTrue(bench.validate(dict(GOOD, term={"number": True, "unit": "years"})))
        self.assertTrue(bench.validate(dict(GOOD, extra=1)))
        self.assertTrue(bench.validate([]))

    def test_rules_baseline(self):
        text = ('This Agreement is made as of March 3, 2010 by and between Acme Corp. (the "Company") and '
                'John Smith ("Employee"). The term of this Agreement shall be two (2) years. This Agreement '
                'shall be governed by the laws of the State of New York.')
        self.assertEqual(bench.rules_extract(text), {
            "parties": ["Acme Corp.", "John Smith"], "effective_date": "2010-03-03",
            "jurisdiction": "New York", "term": {"number": 2, "unit": "years"}})

    def test_deepseek_offpeak_price(self):
        usage = {"input": 1_000_000, "cached_input": 0, "output": 0, "reasoning": 0}
        peak = dt.datetime(2026, 9, 29, 2, 0, tzinfo=dt.timezone.utc)      # Tuesday 02:00 UTC
        off = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)
        self.assertAlmostEqual(bench.cost_usd(PRICE["deepseek"], usage, peak), 0.30)
        self.assertAlmostEqual(bench.cost_usd(PRICE["deepseek"], usage, off), 0.15)

    def test_scoring(self):
        docs = [{"id": "a", "gold": {("party", "X"), ("party", "Y"), ("term", "2_YEARS")}},
                {"id": "b", "gold": {("jurisdiction", "OHIO")}}]
        recs = [{"doc_id": "a", "status": "ok", "pred": [["party", "X"], ["term", "3_YEARS"]]}]
        s = bench.summarize(recs, docs)
        self.assertAlmostEqual(s["f1"], 2 * 1 / (2 + 4))
        self.assertEqual(s["missing"], 1)


class KeyFileTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in bench.KEY_NAMES + ("LLM_KEYS_FILE",)}
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "keys.env"

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_reads_known_names_only_and_the_environment_wins(self):
        self.path.write_text("# comment\n\nOPENAI_API_KEY=sk-one\nexport ANTHROPIC_API_KEY = 'sk-two'\n"
                             "DEEPSEEK_API_KEY=\nOTHER_SECRET=x\nnot a line\n")
        os.chmod(self.path, 0o600)
        os.environ["ANTHROPIC_API_KEY"] = "from-env"
        self.assertEqual(bench.load_key_file(self.path), ["OPENAI_API_KEY"])
        self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-one")
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "from-env")
        self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
        self.assertNotIn("OTHER_SECRET", os.environ)

    def test_quotes_env_path_and_missing_file(self):
        self.path.write_text('ANTHROPIC_API_KEY="sk-two"\n')
        os.chmod(self.path, 0o600)
        os.environ["LLM_KEYS_FILE"] = str(self.path)
        self.assertEqual(bench.load_key_file(), ["ANTHROPIC_API_KEY"])
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "sk-two")
        self.assertEqual(bench.load_key_file(Path(self.tmp.name) / "none.env"), [])

    def test_warns_when_others_can_read_it(self):
        import contextlib
        import io
        self.path.write_text("OPENAI_API_KEY=sk-one\n")
        os.chmod(self.path, 0o644)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bench.load_key_file(self.path)
        self.assertIn("chmod 600", out.getvalue())
        self.assertNotIn("sk-one", out.getvalue())


class StatsTests(unittest.TestCase):
    def test_bootstrap_interval_and_pairing(self):
        docs = [{"id": f"d{i}", "gold": {("party", "X")}} for i in range(40)]
        right = [{"doc_id": f"d{i}", "pred": [["party", "X"]]} for i in range(40)]
        half = [{"doc_id": f"d{i}", "pred": [["party", "X" if i % 2 else "Y"]]} for i in range(40)]
        counts = {"right": bench.doc_counts(right, docs), "half": bench.doc_counts(half, docs)}
        boot = bench.bootstrap_f1(counts, len(docs), resamples=300, seed=1)
        self.assertEqual(set(boot["right"]), {1.0})
        lo, hi = bench.interval(boot["half"])
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)
        self.assertEqual(boot, bench.bootstrap_f1(counts, len(docs), resamples=300, seed=1))
        same = bench.bootstrap_f1({"a": counts["half"], "b": counts["half"]}, len(docs), resamples=50)
        self.assertEqual(same["a"], same["b"])  # paired: every run is scored on the same resamples

    def test_shared_cases(self):
        docs = [{"id": "a", "gold": {("party", "X"), ("term", "2_YEARS")}},
                {"id": "b", "gold": {("party", "Z")}}]

        def run(a_party, b_party):
            return [{"doc_id": "a", "pred": [["party", a_party], ["term", "2_YEARS"]]},
                    {"doc_id": "b", "pred": [["party", b_party]]}]
        runs = {"m1": run("Y", "Z"), "m2": run("Y", "Z"), "m3": run("Y", "Q"), "m4": run("X", "Q")}
        cases, need = bench.shared_cases(runs, docs)
        self.assertEqual(need, 3)
        self.assertEqual([(c["doc_id"], c["field"], c["answer"], c["runs"]) for c in cases],
                         [("a", "party", ["Y"], 3)])
        self.assertEqual(cases[0]["errors"], {"m1": 2, "m2": 2, "m3": 2, "m4": 0})


if __name__ == "__main__":
    unittest.main()
