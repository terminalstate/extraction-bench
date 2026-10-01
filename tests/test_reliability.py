import argparse
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench  # noqa: E402
import reliability as rel  # noqa: E402
from fake_api import FakeAPI, anthropic_ok, deepseek_ok, openai_ok  # noqa: E402

GOOD = {"parties": ["NIKE, Inc.", "Eric Dean Sprunk"], "effective_date": "2001-04-18",
        "jurisdiction": "State of Oregon", "term": {"number": 2, "unit": "years"}}
KEYS = ["effective_date", "jurisdiction", "party", "term"]
GOLD = {("party", "NIKE_INC."), ("party", "ERIC_DEAN_SPRUNK"), ("effective_date", "2001-04-18"),
        ("jurisdiction", "OREGON"), ("term", "2_YEARS")}
DOC = {"id": "doc1", "keys": KEYS, "text": "agreement text", "gold": GOLD}
PRICE = {"openai": {"provider": "openai", "input": 0.10, "cached_input": 0.01, "output": 0.50},
         "deepseek": {"provider": "deepseek", "input": 0.30, "cached_input": 0.006, "output": 1.20,
                      "offpeak_factor": 0.5},
         "anthropic": {"provider": "anthropic", "input": 1.0, "cached_input": 0.1, "output": 5.0}}
CHAT, BETA, MESSAGES = "/chat/completions", "/beta/chat/completions", "/v1/messages"


def cfg(**kw):
    base = dict(timeout=0.5, deadline=0.0, max_attempts=3, max_output_tokens=1000, bigger_factor=4,
                reasoning_effort="low", backoff_base=0.01, backoff_cap=0.05, workers=2, limit=0, max_usd=0,
                cells="", extended=False, no_burst=False, resamples=100, quiet=True, max_failed_in_row=6)
    base.update(kw)
    return argparse.Namespace(**base)


def tool_call_ok(obj=None, arguments=None, finish="tool_calls", reasoning=None, provider="openai"):
    msg = {"content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {
        "name": "record_fields", "arguments": arguments if arguments is not None else json.dumps(obj)}}]}
    if reasoning:
        msg["reasoning_content"] = reasoning
    usage = {"prompt_tokens": 1000, "completion_tokens": 50}
    return {"status": 200, "body": {"choices": [{"message": msg, "finish_reason": finish}], "usage": usage}}


def anthropic_text(text, stop_reason="end_turn"):
    return {"status": 200, "body": {"content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
                                    "usage": {"input_tokens": 1000, "output_tokens": 80}}}


class RequestTests(unittest.TestCase):
    def test_openai_bodies(self):
        b = {m: rel.build(rel.Cell("gpt-6-luna", m), "openai", "t", 500) for m in rel.MECHANISMS}
        self.assertNotIn("response_format", b["prompt"])
        self.assertNotIn("tools", b["prompt"])
        self.assertEqual(b["json_mode"]["response_format"], {"type": "json_object"})
        self.assertEqual(b["strict"]["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(b["tool"]["tool_choice"], {"type": "function", "function": {"name": "record_fields"}})
        self.assertNotIn("strict", b["tool"]["tools"][0]["function"])
        for body in b.values():
            self.assertEqual(body["max_completion_tokens"], 500)
            self.assertEqual(body["reasoning_effort"], "low")
            self.assertEqual(body["messages"][0]["content"], bench.SYSTEM_PROMPT)
        off = rel.build(rel.Cell("gpt-6-luna", "tool", thinking=False), "openai", "t", 500)
        self.assertEqual(off["reasoning_effort"], "none")       # function tools need it in Chat Completions
        self.assertEqual(rel.Cell("gpt-6-luna", "tool", thinking=False).name, "gpt-6-luna__tool__nothink")

    def test_deepseek_bodies_and_endpoint(self):
        tool = rel.build(rel.Cell("deepseek-flash", "tool"), "deepseek", "t", 500)
        strict = rel.build(rel.Cell("deepseek-flash", "strict"), "deepseek", "t", 500)
        strict_nt = rel.build(rel.Cell("deepseek-flash", "strict", thinking=False), "deepseek", "t", 500)
        json_nt = rel.build(rel.Cell("deepseek-flash", "json_mode", thinking=False), "deepseek", "t", 500)
        self.assertEqual(tool["tool_choice"], "auto")          # forced choice is a 400 in thinking mode
        self.assertNotIn("strict", tool["tools"][0]["function"])
        self.assertTrue(strict["tools"][0]["function"]["strict"])
        self.assertEqual(strict["tool_choice"], "auto")
        self.assertNotIn("thinking", strict)
        self.assertEqual(strict_nt["thinking"], {"type": "disabled"})
        self.assertEqual(strict_nt["tool_choice"]["function"]["name"], "record_fields")
        self.assertEqual(json_nt["response_format"], {"type": "json_object"})
        self.assertEqual(json_nt["max_tokens"], 500)
        self.assertTrue(tool["messages"][1]["content"].endswith(rel.ASK_TOOL))     # the tool is optional here
        self.assertTrue(strict["messages"][1]["content"].endswith(rel.ASK_TOOL))
        self.assertFalse(strict_nt["messages"][1]["content"].endswith(rel.ASK_TOOL))  # forced: prompt unchanged
        os.environ.pop("DEEPSEEK_BASE_URL", None)
        self.assertTrue(rel.endpoint("deepseek", rel.Cell("deepseek-flash", "strict"))[0].endswith(BETA))
        self.assertTrue(rel.endpoint("deepseek", rel.Cell("deepseek-flash", "tool"))[0].endswith(
            "deepseek.com" + CHAT))

    def test_anthropic_bodies(self):
        prompt = rel.build(rel.Cell("claude-haiku-4-5", "prompt"), "anthropic", "t", 500)
        tool = rel.build(rel.Cell("claude-haiku-4-5", "tool"), "anthropic", "t", 500)
        strict = rel.build(rel.Cell("claude-haiku-4-5", "strict"), "anthropic", "t", 500)
        self.assertNotIn("tools", prompt)
        self.assertNotIn("output_config", prompt)
        self.assertEqual(tool["tool_choice"], {"type": "tool", "name": "record_fields"})
        self.assertEqual(strict["output_config"], {"format": {"type": "json_schema", "schema": bench.SCHEMA}})
        with self.assertRaises(ValueError):
            rel.build(rel.Cell("claude-haiku-4-5", "json_mode"), "anthropic", "t", 500)

    def test_nonull_variant(self):
        cell = rel.Cell("deepseek-flash", "strict", variant="nonull")
        body = rel.build(cell, "deepseek", "t", 500)
        self.assertIs(body["tools"][0]["function"]["parameters"], rel.NONULL_SCHEMA)
        self.assertIn("no null", body["messages"][0]["content"])
        self.assertEqual(rel.denull({"parties": ["A"], "effective_date": "", "jurisdiction": "", "term": []}),
                         {"parties": ["A"], "effective_date": None, "jurisdiction": None, "term": None})
        self.assertEqual(rel.denull({"term": [{"number": 2, "unit": "years"}]})["term"],
                         {"number": 2, "unit": "years"})
        self.assertEqual(cell.name, "deepseek-flash__strict-nonull")
        self.assertEqual(rel.cell_from_name("deepseek-flash__strict-nonull__nothink__burst").__dict__,
                         rel.Cell("deepseek-flash", "strict", False, True, "nonull").__dict__)
        rep = rel.Cell("claude-haiku-4-5", "tool", repeat=True)
        self.assertEqual(rep.name, "claude-haiku-4-5__tool__repeat")
        self.assertEqual(rel.cell_from_name(rep.name).__dict__, rep.__dict__)
        prices = bench.load_prices()
        self.assertTrue(rel.is_bench_cell(rel.Cell("claude-haiku-4-5", "tool"), prices))
        self.assertFalse(rel.is_bench_cell(rep, prices))

    def test_parse(self):
        cell = rel.Cell("gpt-6-luna", "tool")
        r = rel.parse("openai", cell, tool_call_ok(GOOD)["body"])
        self.assertEqual((r["tool_called"], json.loads(r["text"])), (True, GOOD))
        r = rel.parse("deepseek", rel.Cell("deepseek-flash", "tool"), deepseek_ok(GOOD)["body"])
        self.assertIs(r["tool_called"], False)
        r = rel.parse("anthropic", rel.Cell("claude-haiku-4-5", "tool"), anthropic_ok(GOOD)["body"])
        self.assertEqual((r["obj"], r["finish"]), (GOOD, "tool_use"))
        r = rel.parse("anthropic", rel.Cell("claude-haiku-4-5", "strict"), anthropic_text(json.dumps(GOOD))["body"])
        self.assertEqual((json.loads(r["text"]), r["tool_called"]), (GOOD, None))


class ExamineTests(unittest.TestCase):
    cell = rel.Cell("gpt-6-luna", "prompt")

    def cat(self, text=None, obj=None, finish="stop", tool_called=None, cell=None):
        return rel.examine({"text": text, "obj": obj, "finish": finish, "tool_called": tool_called},
                           cell or self.cell)[0]

    def test_categories(self):
        self.assertEqual(self.cat(json.dumps(GOOD)), "ok")
        self.assertEqual(self.cat("```json\n" + json.dumps(GOOD) + "\n```"), "wrapped")
        self.assertEqual(self.cat("Here are the fields: " + json.dumps(GOOD) + " Let me know."), "wrapped")
        self.assertEqual(self.cat('{"parties": ["A", '), "invalid_json")
        bad_key = dict(GOOD)
        bad_key["partties"] = bad_key.pop("parties")
        self.assertEqual(self.cat(json.dumps(bad_key)), "schema")
        self.assertEqual(self.cat(json.dumps(dict(GOOD, term={"number": 2, "unit": "year"}))), "schema")
        self.assertEqual(self.cat(json.dumps(dict(GOOD, effective_date="2004"))), "value")
        self.assertEqual(self.cat(json.dumps(dict(GOOD, effective_date=2004))), "schema")
        self.assertEqual(self.cat(json.dumps(dict(GOOD, term={"number": 0, "unit": "years"}))), "value")
        self.assertEqual(self.cat("", finish="length"), "truncated")
        self.assertEqual(self.cat(obj={"parties": []}, finish="max_tokens"), "truncated")
        self.assertEqual(self.cat("", finish="insufficient_system_resource"), "stopped")
        self.assertEqual(self.cat("I can't help", finish="refusal"), "refusal")
        self.assertEqual(self.cat("   "), "empty")
        self.assertEqual(self.cat(json.dumps(GOOD), tool_called=False), "no_tool_call")
        self.assertEqual(self.cat(obj=GOOD, finish="tool_use"), "ok")

    def test_nonull_answers_are_mapped_back(self):
        cell = rel.Cell("deepseek-flash", "strict", variant="nonull")
        ans = dict(GOOD, effective_date="", jurisdiction="", term=[])
        category, _, obj = rel.examine({"text": json.dumps(ans), "finish": "tool_calls", "tool_called": True}, cell)
        self.assertEqual(category, "ok")
        self.assertEqual((obj["effective_date"], obj["jurisdiction"], obj["term"]), (None, None, None))


class RepairTests(unittest.TestCase):
    def fix(self, text=None, obj=None):
        return rel.repair({"text": text, "obj": obj})

    def test_never_changes_a_good_answer(self):
        obj, repairs, dropped = self.fix(json.dumps(GOOD))
        self.assertEqual((obj, repairs, dropped), (GOOD, [], []))

    def test_wrappers(self):
        for text in ("```json\n" + json.dumps(GOOD) + "\n```", "Sure! " + json.dumps(GOOD) + "\nDone.",
                     json.dumps([GOOD]), json.dumps(GOOD)[:-1] + ",}"):
            obj, repairs, dropped = self.fix(text)
            self.assertEqual((obj, dropped), (GOOD, []), text)
            self.assertTrue(repairs, text)

    def test_cut_off_json_keeps_what_is_complete(self):
        text = '{"parties": ["NIKE, Inc.", "Eric Dean Sprunk"], "effective_date": "2001-04-18", "jurisdic'
        obj, repairs, dropped = self.fix(text)
        self.assertEqual(obj["parties"], GOOD["parties"])
        self.assertEqual(obj["effective_date"], "2001-04-18")
        self.assertEqual(dropped, ["jurisdiction", "term"])
        self.assertIn("cut-off json closed", repairs)

    def test_keys_and_values(self):
        messy = {"Partties": "NIKE, Inc.", "effectiveDate": "April 18, 2001", "jurisdiction": "Oregon",
                 "term": {"number": "2", "unit": "Year"}, "notes": "x"}
        obj, repairs, dropped = self.fix(json.dumps(messy))
        self.assertEqual(obj, {"parties": ["NIKE, Inc."], "effective_date": "2001-04-18", "jurisdiction": "Oregon",
                               "term": {"number": 2, "unit": "years"}})
        self.assertEqual(dropped, [])
        self.assertIn("key 'notes' dropped", repairs)

    def test_dates(self):
        for given, want in (("18th day of April, 2001", "2001-04-18"), ("04/18/2001", "2001-04-18"),
                            ("18/04/2001", "2001-04-18"), ("2001-04-18T00:00:00Z", "2001-04-18"),
                            ("Wednesday, April 18, 2001", "2001-04-18")):
            self.assertEqual(rel.read_date(given)[0], want, given)
        for given in ("2004", "April 2004", "04/05/2001", "2001-02-30", 2004):
            self.assertEqual(rel.read_date(given), (None, "dropped"), given)
        self.assertEqual(rel.read_date("not stated"), (None, "read as not stated"))

    def test_terms(self):
        self.assertEqual(rel.read_term("two (2) years")[0], {"number": 2, "unit": "years"})
        self.assertEqual(rel.read_term("a period of 18 months")[0], {"number": 18, "unit": "months"})
        self.assertEqual(rel.read_term("24-month")[0], {"number": 24, "unit": "months"})
        self.assertEqual(rel.read_term("until terminated"), (None, "dropped"))
        self.assertEqual(rel.read_term({"number": 0, "unit": "years"}), (None, "dropped"))
        self.assertEqual(rel.read_term({"number": 3, "unit": "decades"}), (None, "dropped"))
        self.assertEqual(rel.read_term({"number": 2, "unit": "years"}), ({"number": 2, "unit": "years"}, ""))

    def test_bare_year_is_dropped_not_guessed(self):
        obj, _, dropped = self.fix(obj=dict(GOOD, effective_date="2004"))
        self.assertIsNone(obj["effective_date"])
        self.assertEqual(dropped, ["effective_date"])
        self.assertEqual(obj["parties"], GOOD["parties"])

    def test_parties_shapes_and_missing_keys(self):
        obj, repairs, dropped = self.fix(json.dumps({"parties": [{"name": "A Corp"}, {"role": "x"}, "B Ltd"],
                                                     "jurisdiction": 5}))
        self.assertEqual(obj["parties"], ["A Corp", "B Ltd"])
        self.assertEqual(dropped, ["jurisdiction"])
        self.assertIn("missing term read as not stated", repairs)

    def test_cut_off_or_unreadable_keys_make_missing_fields_unknown(self):
        obj, _, dropped = rel.repair({"obj": {"parties": ["A Corp"]}}, None, "truncated")
        self.assertEqual((obj["parties"], dropped), (["A Corp"], ["effective_date", "jurisdiction", "term"]))
        obj, repairs, dropped = self.fix(json.dumps({"party": ["A Corp"], "governing_law": "Ohio", "notes": 1}))
        self.assertEqual((obj["parties"], obj["jurisdiction"]), (["A Corp"], "Ohio"))
        self.assertEqual(dropped, ["effective_date", "term"])          # 'notes' might have held them
        obj, _, dropped = self.fix(json.dumps({"effective_date": None, "jurisdiction": "Ohio", "term": None}))
        self.assertEqual(dropped, ["parties"])

    def test_nothing_salvaged_is_none(self):
        self.assertIsNone(rel.repair({"obj": {}}, None, "truncated")[0])
        self.assertIsNone(self.fix('{"parties": ["A Co')[0])
        self.assertIsNone(self.fix(json.dumps({"summary": "an NDA"}))[0])

    def test_unreadable(self):
        self.assertEqual(self.fix("no json here")[0], None)
        self.assertEqual(self.fix("")[0], None)
        self.assertEqual(self.fix("[1, 2]")[0], None)


class FakeAPICase(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        os.environ.update({"OPENAI_BASE_URL": self.api.url, "DEEPSEEK_BASE_URL": self.api.url,
                           "ANTHROPIC_BASE_URL": self.api.url, "OPENAI_API_KEY": "k",
                           "DEEPSEEK_API_KEY": "k", "ANTHROPIC_API_KEY": "k"})
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (rel.RUNS_REL, rel.RESULTS, bench.RUNS, bench.load_split)
        rel.RUNS_REL = Path(self.tmp.name) / "runs-rel"
        rel.RESULTS = Path(self.tmp.name) / "reliability-results.md"
        bench.RUNS = Path(self.tmp.name) / "runs"

    def tearDown(self):
        rel.RUNS_REL, rel.RESULTS, bench.RUNS, bench.load_split = self.saved
        self.api.close()
        self.tmp.cleanup()

    def first(self, cell, provider, c=None, state=None, doc=DOC):
        return rel.first_answer(doc, cell, provider, PRICE[provider], c or cfg(), state or bench.RunState(0))

    def recover(self, cell, provider, c=None, doc=DOC):
        rel.recover_doc(doc, cell, provider, PRICE[provider], c or cfg(), bench.RunState(0))
        return json.loads(rel.doc_path(cell, doc["id"]).read_text())


class TransportTests(FakeAPICase):
    def test_trickle_outlives_the_read_timeout(self):
        self.api.queue[CHAT] = [dict(openai_ok(GOOD), trickle={"every": 0.1, "for": 1.2})]
        status, _, raw, info = rel.post(self.api.url + CHAT, {}, {}, timeout=0.5)
        self.assertEqual((status, info["kind"]), (200, "http"))
        self.assertGreaterEqual(info["latency_s"], 1.2)       # over twice the 0.5 s "timeout"
        self.assertGreater(info["keepalive_bytes"], 5)
        self.assertEqual(json.loads(raw)["choices"][0]["message"]["content"], json.dumps(GOOD))

    def test_deadline_ends_a_trickling_call(self):
        self.api.queue[CHAT] = [dict(openai_ok(GOOD), trickle={"every": 0.1, "for": 3.0})]
        t0 = time.monotonic()
        status, _, _, info = rel.post(self.api.url + CHAT, {}, {}, timeout=0.5, deadline=1.0)
        self.assertEqual((status, info["kind"]), (None, "deadline"))
        self.assertLess(time.monotonic() - t0, 2.0)

    def test_429_retry_after_and_rate_limit_headers_recorded(self):
        self.api.queue[MESSAGES] = [{"status": 429, "headers": {"retry-after": "0",
                                                               "anthropic-ratelimit-requests-remaining": "0"},
                                     "body": {"error": "rate"}}, anthropic_ok(GOOD)]
        rec = self.first(rel.Cell("claude-haiku-4-5", "tool"), "anthropic")
        att = rec["first"]["attempts"]
        self.assertEqual([a["status"] for a in att], [429, 200])
        self.assertEqual(att[0]["headers"], {"retry-after": "0", "anthropic-ratelimit-requests-remaining": "0"})
        self.assertEqual(rec["first"]["category"], "ok")

    def test_a_timed_out_request_is_sent_once_more_at_most(self):
        self.api.queue[CHAT] = [dict(openai_ok(GOOD), delay=0.8) for _ in range(4)]
        rec = self.first(rel.Cell("gpt-6-luna", "prompt"), "openai", c=cfg(timeout=0.3, max_attempts=5))
        self.assertEqual([a["kind"] for a in rec["first"]["attempts"]], ["timeout", "timeout"])
        self.assertEqual(rec["first"]["category"], "http_failed")

    def test_transport_failure_is_sent_again_on_resume(self):
        cell = rel.Cell("gpt-6-luna", "prompt")
        self.api.queue[CHAT] = [{"status": 503, "body": {}} for _ in range(3)]
        self.assertEqual(self.first(cell, "openai")["first"]["category"], "http_failed")
        self.api.queue[CHAT] = [openai_ok(GOOD)]
        self.assertEqual(self.first(cell, "openai")["first"]["category"], "ok")
        self.api.queue[CHAT] = [{"status": 422, "body": {"error": "bad"}}]
        other = dict(DOC, id="doc2")
        self.assertEqual(self.first(cell, "openai", doc=other)["first"]["category"], "http_failed")
        self.assertEqual(self.first(cell, "openai", doc=other)["first"]["category"], "http_failed")
        self.assertEqual(len(self.api.requests), 5)       # a rejected request is not sent again

    def test_budget_stop_during_retries_is_not_saved(self):
        state = bench.RunState(0)
        self.api.queue[CHAT] = [{"status": 429, "headers": {"retry-after": "0"}, "body": {}}]
        state.stop("budget reached")
        self.assertIsNone(self.first(rel.Cell("gpt-6-luna", "prompt"), "openai", state=state))
        state2 = bench.RunState(0)
        orig = rel.post

        def post_then_stop(*a, **kw):
            state2.stop("budget reached")
            return orig(*a, **kw)
        rel.post = post_then_stop
        try:
            self.api.queue[CHAT] = [{"status": 429, "headers": {"retry-after": "0"}, "body": {}}]
            self.assertIsNone(self.first(rel.Cell("gpt-6-luna", "json_mode"), "openai", state=state2))
        finally:
            rel.post = orig
        self.assertFalse(rel.doc_path(rel.Cell("gpt-6-luna", "json_mode"), "doc1").exists())

    def test_unreachable_provider_stops_after_failures_in_a_row(self):
        c = cfg(max_attempts=1, max_failed_in_row=3)
        self.api.queue[CHAT] = [{"close": True} for _ in range(10)]
        state = bench.RunState(0)
        cell = rel.Cell("gpt-6-luna", "prompt")
        self.first(cell, "openai", c=c, state=state, doc=dict(DOC, id="a"))
        self.first(cell, "openai", c=c, state=state, doc=dict(DOC, id="b"))
        with self.assertRaises(bench.Fatal) as e:
            self.first(cell, "openai", c=c, state=state, doc=dict(DOC, id="c"))
        self.assertIn("unreachable", str(e.exception))
        self.assertTrue(rel.doc_path(cell, "c").exists())      # the answer that tripped it is kept

    def test_rate_limits_do_not_count_as_unreachable(self):
        c = cfg(max_attempts=1, max_failed_in_row=2)
        self.api.queue[CHAT] = [{"status": 429, "headers": {"retry-after": "0"}, "body": {}} for _ in range(5)]
        state = bench.RunState(0)
        cell = rel.Cell("gpt-6-luna", "strict", burst=True)
        for i in range(5):
            self.assertEqual(self.first(cell, "openai", c=c, state=state, doc=dict(DOC, id=f"d{i}"))["first"]
                             ["category"], "http_failed")
        self.assertEqual(len(rel.load_cell(cell.name)), 5)

    def test_resume_sends_failures_again_except_in_a_burst(self):
        docs = [dict(DOC, id=f"d{i}") for i in range(2)]
        for cell in (rel.Cell("gpt-6-luna", "strict", repeat=True), rel.Cell("gpt-6-luna", "strict", burst=True)):
            self.api.queue[CHAT] = [{"status": 503, "body": {}} for _ in range(6)]
            rel.run_cell(cell, "openai", PRICE["openai"], docs, cfg(max_attempts=3), bench.RunState(0))
            self.api.queue[CHAT] = [openai_ok(GOOD) for _ in range(2)]
            n = len(self.api.requests)
            rel.run_cell(cell, "openai", PRICE["openai"], docs, cfg(max_attempts=3), bench.RunState(0))
            sent = len(self.api.requests) - n
            self.assertEqual(sent, 0 if cell.burst else 2, cell.name)

    def test_400_fails_the_answer_401_stops(self):
        self.api.queue[CHAT] = [{"status": 400, "body": {"error": {"message": "bad schema"}}}]
        rec = self.first(rel.Cell("gpt-6-luna", "prompt"), "openai")
        self.assertEqual(rec["first"]["category"], "http_failed")
        self.assertIn("bad schema", rec["first"]["problems"][0])
        self.api.queue[CHAT] = [{"status": 401, "body": {}}]
        with self.assertRaises(bench.Fatal):
            self.first(rel.Cell("gpt-6-luna", "json_mode"), "openai")


class FlowTests(FakeAPICase):
    def test_wrapped_answer_local_retry_feedback(self):
        cell = rel.Cell("gpt-6-luna", "prompt")
        wrapped = "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```"
        self.api.queue[CHAT] = [openai_ok(content=wrapped), openai_ok(content=wrapped), openai_ok(GOOD)]
        self.assertEqual(self.first(cell, "openai")["first"]["category"], "wrapped")
        rec = self.recover(cell, "openai")
        self.assertEqual(rec["recovery"]["retry"]["category"], "wrapped")
        self.assertEqual(rec["recovery"]["feedback"]["category"], "ok")
        self.assertNotIn("bigger", rec["recovery"])
        _, _, fb = self.api.requests[2]
        self.assertEqual(fb["messages"][2], {"role": "assistant", "content": wrapped})
        self.assertIn("could not be used: the JSON object came with a code fence", fb["messages"][3]["content"])
        oc = rel.outcomes(rec, cell)
        self.assertIsNone(oc["none"][0])
        self.assertEqual(oc["local"][:3], (GOOD, [], 0))
        self.assertIsNone(oc["retry"][0])
        self.assertEqual(oc["feedback"][0], GOOD)
        self.assertEqual(oc["stack"][:3], (GOOD, [], 0))      # local repair was enough: no extra request
        self.assertEqual(len(self.api.requests), 3)

    def test_cut_off_answer_gets_a_larger_limit(self):
        cell = rel.Cell("deepseek-flash", "json_mode")
        self.api.queue[CHAT] = [deepseek_ok(content="", finish="length"), deepseek_ok(content="", finish="length"),
                                deepseek_ok(GOOD)]
        self.first(cell, "deepseek")
        rec = self.recover(cell, "deepseek")
        self.assertEqual(sorted(rec["recovery"]), ["bigger", "retry"])
        self.assertEqual([b["max_tokens"] for _, _, b in self.api.requests], [1000, 1000, 4000])
        oc = rel.outcomes(rec, cell)
        self.assertIsNone(oc["local"][0])
        self.assertEqual(oc["bigger"][0], GOOD)
        self.assertEqual(oc["stack"][:3], (GOOD, [], 1))
        del rec["recovery"]["bigger"]
        self.assertIsNone(rel.outcomes(rec, cell)["stack"])      # its request was not made: not tried

    def test_bigger_gets_longer_time_limits(self):
        cell = rel.Cell("deepseek-flash", "json_mode")
        self.api.queue[CHAT] = [deepseek_ok(content="", finish="length"), deepseek_ok(content="", finish="length"),
                                dict(deepseek_ok(GOOD), delay=0.6)]
        c = cfg(timeout=0.3)
        self.first(cell, "deepseek", c=c)
        rec = self.recover(cell, "deepseek", c=c)
        self.assertEqual(rec["recovery"]["bigger"]["category"], "ok")     # 0.6 s < 4 x 0.3 s

    def test_fallback_cell_gets_recovery(self):
        cell = rel.Cell("deepseek-flash", "strict", thinking=False)
        bad = dict(GOOD, effective_date="2004", term=[])
        self.api.queue[BETA] = [{"status": 422, "body": {"error": "schema"}}, tool_call_ok(bad), tool_call_ok(bad),
                                tool_call_ok(dict(GOOD, term=[{"number": 2, "unit": "years"}]))]
        rel.run_cell(cell, "deepseek", PRICE["deepseek"], [DOC], cfg(), bench.RunState(0))
        rel.recover_cell(cell, "deepseek", PRICE["deepseek"], [DOC], cfg(), bench.RunState(0))
        rec = rel.load_cell("deepseek-flash__strict-nonull__nothink")[0]
        self.assertEqual(rec["first"]["category"], "value")
        self.assertEqual((rec["recovery"]["retry"]["category"], rec["recovery"]["feedback"]["category"]),
                         ("value", "ok"))

    def test_anthropic_tool_feedback_is_a_tool_result(self):
        cell = rel.Cell("claude-haiku-4-5", "tool")
        self.api.queue[MESSAGES] = [anthropic_ok(dict(GOOD, effective_date="2004")),
                                    anthropic_ok(dict(GOOD, effective_date="2004")), anthropic_ok(GOOD)]
        self.assertEqual(self.first(cell, "anthropic")["first"]["category"], "value")
        rec = self.recover(cell, "anthropic")
        _, _, fb = self.api.requests[2]
        self.assertEqual(fb["messages"][1]["role"], "assistant")
        result = fb["messages"][2]["content"][0]
        self.assertEqual((result["type"], result["tool_use_id"], result["is_error"]), ("tool_result", "t1", True))
        self.assertIn("2004", result["content"])
        oc = rel.outcomes(rec, cell)
        self.assertEqual(oc["local"][1], ["effective_date"])     # partial: the date is emptied, not guessed
        self.assertEqual(oc["stack"][:3], (GOOD, [], 1))

    def test_deepseek_tool_feedback_echoes_reasoning(self):
        cell = rel.Cell("deepseek-flash", "tool")
        bad = json.dumps({"partties": ["A"], "effective_date": None, "jurisdiction": None, "term": None})
        self.api.queue[CHAT] = [tool_call_ok(arguments=bad, reasoning="thinking..."), tool_call_ok(GOOD),
                                tool_call_ok(GOOD)]
        self.assertEqual(self.first(cell, "deepseek")["first"]["category"], "schema")
        rec = self.recover(cell, "deepseek")
        _, _, fb = self.api.requests[2]
        echo, tool = fb["messages"][2], fb["messages"][3]
        self.assertEqual(echo["reasoning_content"], "thinking...")
        self.assertEqual(echo["tool_calls"][0]["id"], "call_1")
        self.assertEqual((tool["role"], tool["tool_call_id"]), ("tool", "call_1"))
        self.assertEqual(rec["recovery"]["feedback"]["category"], "ok")
        self.assertEqual(rel.outcomes(rec, cell)["local"][0]["parties"], ["A"])   # partties read as parties

    def test_no_tool_call_asks_for_the_tool(self):
        cell = rel.Cell("deepseek-flash", "strict")
        self.api.queue[BETA] = [deepseek_ok(GOOD), deepseek_ok(GOOD), tool_call_ok(GOOD)]
        self.assertEqual(self.first(cell, "deepseek")["first"]["category"], "no_tool_call")
        rec = self.recover(cell, "deepseek")
        _, _, fb = self.api.requests[2]
        self.assertEqual(fb["messages"][3]["content"], rel.ASK_TOOL)
        self.assertEqual(rel.outcomes(rec, cell)["local"][0], GOOD)   # the JSON it wrote in text is usable

    def test_rejected_strict_schema_falls_back_to_nonull(self):
        cell = rel.Cell("deepseek-flash", "strict", thinking=False)
        nonull = dict(GOOD, effective_date="", term=[{"number": 2, "unit": "years"}])
        self.api.queue[BETA] = [{"status": 400, "body": {"error": {"message": "unsupported type null"}}}] * 2 + \
            [tool_call_ok(nonull) for _ in range(3)]
        docs = [dict(DOC, id=f"d{i}") for i in range(3)]
        rel.run_cell(cell, "deepseek", PRICE["deepseek"], docs, cfg(), bench.RunState(0))
        meta = rel.read_meta(cell.name)
        self.assertIn("unsupported type null", meta["skipped"])
        self.assertEqual(meta["fallback"], "nonull")
        recs = rel.load_cell("deepseek-flash__strict-nonull__nothink")
        self.assertEqual([r["first"]["category"] for r in recs], ["ok"] * 3)
        self.assertIsNone(recs[0]["first"]["obj"]["effective_date"])
        self.assertIn("no null", self.api.requests[2][2]["messages"][0]["content"])

    def test_burst_sends_every_document_at_once(self):
        cell = rel.Cell("gpt-6-luna", "strict", burst=True)
        self.api.queue[CHAT] = [dict(openai_ok(GOOD), delay=0.4) for _ in range(6)]
        docs = [dict(DOC, id=f"d{i}") for i in range(6)]
        t0 = time.monotonic()
        rel.run_cell(cell, "openai", PRICE["openai"], docs, cfg(workers=1), bench.RunState(0))
        self.assertLess(time.monotonic() - t0, 1.5)       # 6 x 0.4 s one after another would take 2.4 s
        self.assertEqual(rel.read_meta(cell.name)["workers"], 6)

    def test_repeat_runs_without_a_smoke_test(self):
        cell = rel.Cell("gpt-6-luna", "strict", repeat=True)
        self.api.queue[CHAT] = [openai_ok(GOOD) for _ in range(3)]
        rel.run_cell(cell, "openai", PRICE["openai"], [dict(DOC, id=f"d{i}") for i in range(3)], cfg(workers=2),
                     bench.RunState(0))
        self.assertEqual(len(self.api.requests), 3)
        self.assertEqual(rel.read_meta(cell.name)["workers"], 2)

    def test_fatal_in_the_pool_stops_the_cell(self):
        cell = rel.Cell("gpt-6-luna", "strict", repeat=True)
        self.api.queue[CHAT] = [{"status": 401, "body": {}} for _ in range(10)]
        with self.assertRaises(bench.Fatal):
            rel.run_cell(cell, "openai", PRICE["openai"], [dict(DOC, id=f"d{i}") for i in range(10)],
                         cfg(workers=1), bench.RunState(0))
        self.assertEqual(len(self.api.requests), 1)

    def test_402_stops_the_provider(self):
        self.api.queue[CHAT] = [{"status": 402, "body": {"error": {"message": "Insufficient Balance"}}}]
        with self.assertRaises(bench.Fatal):
            self.first(rel.Cell("deepseek-flash", "prompt"), "deepseek")

    def test_budget_stops_before_the_next_request(self):
        state = bench.RunState(max_usd=1e-9)
        self.api.queue[CHAT] = [openai_ok(GOOD), openai_ok(GOOD)]
        self.first(rel.Cell("gpt-6-luna", "prompt"), "openai", state=state)
        self.assertIsNone(self.first(rel.Cell("gpt-6-luna", "prompt"), "openai", state=state,
                                     doc=dict(DOC, id="doc2")))
        self.assertEqual(len(self.api.requests), 1)

    def test_bench_run_is_imported_not_changed(self):
        run = bench.RUNS / "deepseek-flash__v1__dev-0"
        (run / "docs").mkdir(parents=True)
        (run / "meta.json").write_text(json.dumps({"model": "deepseek-flash", "started": "2026-09-30T02:00:00+00:00"}))
        cut = deepseek_ok(content="", finish="length")["body"]
        good = deepseek_ok(GOOD)["body"]
        at = {"at": "2026-09-30T02:00:00+00:00", "latency_s": 3.0, "status": 200, "kind": "http"}
        docs = {"a": {"doc_id": "a", "keys": KEYS, "attempts": [at], "outputs": [{"finish": "stop", "raw": good}]},
                "b": {"doc_id": "b", "keys": KEYS, "attempts": [dict(at, status=503), at, at],
                      "outputs": [{"finish": "length", "raw": cut}, {"finish": "stop", "raw": good}]}}
        for k, v in docs.items():
            (run / "docs" / f"{k}.json").write_text(json.dumps(v))
        before = sorted(p.read_text() for p in (run / "docs").glob("*.json"))
        cell = rel.Cell("deepseek-flash", "json_mode")
        self.assertEqual(rel.import_bench_cell(cell, "deepseek", PRICE["deepseek"]), 2)
        a, b = rel.load_cell(cell.name)
        self.assertEqual(a["first"]["category"], "ok")
        self.assertEqual((b["first"]["category"], b["recovery"]["retry"]["category"]), ("truncated", "ok"))
        self.assertEqual([x["status"] for x in b["first"]["attempts"]], [503, 200])
        self.assertEqual(sorted(p.read_text() for p in (run / "docs").glob("*.json")), before)
        self.assertEqual(rel.read_meta(cell.name)["source"], "bench")


class ReportTests(FakeAPICase):
    def test_report_from_cells(self):
        docs = [dict(DOC, id=f"d{i}") for i in range(4)]
        bench.load_split = lambda split, text_col="text_best": docs
        prompt = rel.Cell("gpt-6-luna", "prompt")
        wrapped = "```json\n" + json.dumps(GOOD) + "\n```"
        self.api.queue[CHAT] = [openai_ok(GOOD), openai_ok(content=wrapped), openai_ok(GOOD),
                                openai_ok(content="", finish="length")]
        for d in docs:
            self.first(prompt, "openai", doc=d)
        base, burst = rel.Cell("gpt-6-luna", "strict"), rel.Cell("gpt-6-luna", "strict", burst=True)
        for cell in (base, burst):
            self.api.queue[CHAT] = [openai_ok(GOOD) for _ in docs]
            for d in docs:
                self.first(cell, "openai", doc=d)
        rel.write_meta(burst, workers=4, wall_s=0.5)
        rejected = rel.Cell("gpt-6-luna", "tool")
        rel.write_meta(rejected, skipped='http 400: {\n  "error": {\n    "message": "Function tools | not supported.",\n')
        (rel.RUNS_REL / rejected.name / "docs").mkdir(parents=True)
        rel.cmd_report(cfg())
        text = rel.RESULTS.read_text()
        for part in ("## 1. First answers", "| gpt-6-luna__prompt | 4/4 | 2 | 1 | 0 | 0 | 0 | 1 |",
                     "## 2. What gets an unusable answer back", "| wrapped | 1 | 1/1 |",
                     "## 3. Output tokens", "## 4. All documents at once", "### The same requests, run again",
                     "| gpt-6-luna | 4 | 4 | 1.000 / — / 1.000 | — | +0.000 (+0.000 to +0.000) |",
                     "## 5. What a deadline would cut",
                     "| gpt-6-luna__tool | — | rejected by the API: Function tools / not supported. |"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
