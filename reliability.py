#!/usr/bin/env python3
"""Structured output on a bad day: how often a model's answer is unusable, why, and what gets it back.

A second study on the stand of bench.py: the same task (Kleister NDA, dev-0), prompt, schema, scoring and
prices. What changes is how the answer is requested (prompt only, JSON mode, tool call, strict schema),
the concurrency, and what happens after an unusable answer: a local repair, a plain retry, a retry that
tells the model what was wrong, a retry with a larger output limit. Python 3.9+, standard library only.

  python3 reliability.py plan                  # the cells and their expected cost
  python3 reliability.py run --ask-keys        # per cell: 2-document smoke test, first answers; then recovery
  python3 reliability.py run --cells luna --limit 5
  python3 reliability.py report                # reliability-results.md

API keys: as in bench.py (environment, ~/.config/extraction-bench/keys.env, or --ask-keys).

The bench.py runs in runs/ are reused as cells with the mechanism they used (BENCH_MECHANISM); they are
read, never re-run or changed. Everything new is written to runs-rel/.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import http.client
import json
import os
import random
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import bench
from bench import SCHEMA, SYSTEM_PROMPT, USER_TEMPLATE, Fatal, RunState

ROOT = bench.ROOT
RUNS_REL = ROOT / "runs-rel"
RESULTS = ROOT / "reliability-results.md"
SPLIT = "dev-0"

TOOL_NAME = "record_fields"
TOOL_DESC = "Record the fields extracted from the agreement."
FEEDBACK_TEXT = ("That answer could not be used: {problems}. Reply with only the corrected JSON object, in "
                 "exactly the shape given.")
FEEDBACK_TOOL = "That call could not be used: {problems}. Call " + TOOL_NAME + " again with corrected arguments."
ASK_TOOL = "Record the fields by calling " + TOOL_NAME + "."
KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}

# ---------------------------------------------------------------- cells


class Cell:
    """One way of asking one model: output mechanism, thinking on or off (DeepSeek: `thinking` disabled;
    OpenAI: reasoning_effort none instead of low), and for the repeats of a bench.py run, whether all
    documents go at once (burst) or 4 at a time (repeat)."""

    def __init__(self, model: str, mechanism: str, thinking: bool = True, burst: bool = False, variant: str = "",
                 repeat: bool = False):
        self.model, self.mechanism, self.thinking, self.variant = model, mechanism, thinking, variant
        self.burst, self.repeat = burst, repeat

    @property
    def name(self) -> str:
        parts = [self.model, self.mechanism + (f"-{self.variant}" if self.variant else "")]
        if not self.thinking:
            parts.append("nothink")
        if self.repeat:
            parts.append("repeat")
        if self.burst:
            parts.append("burst")
        return "__".join(parts)

    def with_variant(self, variant: str) -> "Cell":
        return Cell(self.model, self.mechanism, self.thinking, self.burst, variant, self.repeat)

    def __repr__(self):
        return self.name


MECHANISMS = ("prompt", "json_mode", "tool", "strict")
# How bench.py asked each provider. Its runs are these cells, with 4 workers.
BENCH_MECHANISM = {"openai": "strict", "anthropic": "tool", "deepseek": "json_mode"}
BENCH_MODELS = ("gpt-6-luna", "gpt-6-sol", "claude-haiku-4-5", "claude-sonnet-5", "deepseek-flash", "deepseek-v4-pro")

# One inexpensive model per provider, every mechanism its API offers. Then each of the three bench runs is
# repeated twice in a row: 4 requests at a time (repeat), then every document at once (burst). The repeat is
# the same-hour baseline for the burst, and the three runs of the same requests show how much answers move.
BASE_CELLS = [
    Cell("gpt-6-luna", "prompt"), Cell("gpt-6-luna", "json_mode"), Cell("gpt-6-luna", "tool"),
    # the API refuses the tool cell above with reasoning on; these two run with reasoning off, side by side
    Cell("gpt-6-luna", "strict", thinking=False), Cell("gpt-6-luna", "tool", thinking=False),
    Cell("deepseek-flash", "prompt"), Cell("deepseek-flash", "tool"), Cell("deepseek-flash", "strict"),
    Cell("deepseek-flash", "json_mode", thinking=False), Cell("deepseek-flash", "strict", thinking=False),
    Cell("claude-haiku-4-5", "prompt"), Cell("claude-haiku-4-5", "strict"),
    Cell("gpt-6-luna", "strict", repeat=True), Cell("gpt-6-luna", "strict", burst=True),
    Cell("deepseek-flash", "json_mode", repeat=True), Cell("deepseek-flash", "json_mode", burst=True),
    Cell("claude-haiku-4-5", "tool", repeat=True), Cell("claude-haiku-4-5", "tool", burst=True),
]
# Optional: does a larger model follow a prompt-only format better?
EXTENDED_CELLS = [Cell("gpt-6-sol", "prompt"), Cell("deepseek-v4-pro", "prompt"), Cell("claude-sonnet-5", "prompt")]


def provider_of(model: str, prices: dict) -> str:
    return prices[model]["provider"]


def bench_cells(prices: dict) -> list:
    return [Cell(m, BENCH_MECHANISM[provider_of(m, prices)]) for m in BENCH_MODELS if m in prices]


def bench_run_dir(cell: Cell) -> Path:
    return bench.RUNS / f"{cell.model}__{bench.PROMPT_VERSION}__{SPLIT}"


def is_bench_cell(cell: Cell, prices: dict) -> bool:
    return (not cell.burst and not cell.repeat and cell.thinking and not cell.variant and cell.model in BENCH_MODELS
            and cell.mechanism == BENCH_MECHANISM[provider_of(cell.model, prices)])

# ---------------------------------------------------------------- requests


TERM_OBJECT = SCHEMA["properties"]["term"]["anyOf"][1]
# DeepSeek's strict mode does not document a null type. If it rejects SCHEMA, the run falls back to this
# variant: "" for a missing date or jurisdiction, a list of zero or one terms. denull() maps it back.
NONULL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": list(SCHEMA["required"]),
    "properties": {"parties": SCHEMA["properties"]["parties"],
                   "effective_date": {"type": "string"}, "jurisdiction": {"type": "string"},
                   "term": {"type": "array", "items": TERM_OBJECT}},
}
NONULL_NOTE = ('\n\nThis response format has no null: write "" for a date or jurisdiction that is not stated, and '
               'give term as a list with one item, or an empty list if the agreement does not state it.')


def denull(obj):
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for k in ("effective_date", "jurisdiction"):
        if out.get(k) == "":
            out[k] = None
    t = out.get("term")
    if isinstance(t, list) and len(t) <= 1:
        out["term"] = t[0] if t else None
    return out


def uses_tool(provider: str, cell: Cell) -> bool:
    return cell.mechanism == "tool" or (cell.mechanism == "strict" and provider == "deepseek")


def tool_is_optional(provider: str, cell: Cell) -> bool:
    """DeepSeek answers a forced tool choice in thinking mode with HTTP 400, so there the tool is offered
    ("auto") and the user message asks for it."""
    return provider == "deepseek" and cell.thinking and uses_tool(provider, cell)


def endpoint(provider: str, cell: Cell):
    if provider == "anthropic":
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        return f"{base}/v1/messages", {"x-api-key": os.environ.get(KEY_ENV[provider], ""),
                                       "anthropic-version": "2023-06-01"}
    if provider == "openai":
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        return f"{base}/chat/completions", {"authorization": f"Bearer {os.environ.get(KEY_ENV[provider], '')}"}
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    path = "/beta/chat/completions" if cell.mechanism == "strict" else "/chat/completions"  # strict tools: beta
    return base + path, {"authorization": f"Bearer {os.environ.get(KEY_ENV[provider], '')}"}


def build(cell: Cell, provider: str, text: str, max_tokens: int, extra=(), effort: str = "low") -> dict:
    """The request body. `extra` continues the conversation (used by the feedback retry)."""
    schema = NONULL_SCHEMA if cell.variant == "nonull" else SCHEMA
    system = SYSTEM_PROMPT + (NONULL_NOTE if cell.variant == "nonull" else "")
    user = USER_TEMPLATE.format(text=text)
    if tool_is_optional(provider, cell):
        user += "\n\n" + ASK_TOOL      # the prompt asks for a JSON object; say which way to give it
    m = cell.mechanism
    if m not in MECHANISMS:
        raise ValueError(f"unknown mechanism {m!r}")
    if provider == "anthropic":
        body = {"model": cell.model, "max_tokens": max_tokens, "system": system,
                "messages": [{"role": "user", "content": user}, *extra]}
        if m == "tool":
            body["tools"] = [{"name": TOOL_NAME, "description": TOOL_DESC, "input_schema": schema}]
            body["tool_choice"] = {"type": "tool", "name": TOOL_NAME}
        elif m == "strict":
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        elif m == "json_mode":
            raise ValueError("the Anthropic API has no JSON mode")
        return body
    body = {"model": cell.model, "messages": [{"role": "system", "content": system},
                                              {"role": "user", "content": user}, *extra]}
    if provider == "openai":
        body["max_completion_tokens"] = max_tokens
        # Chat Completions takes function tools from gpt-6 models only with reasoning_effort "none" (HTTP 400
        # otherwise, seen 2026-09-30); cells with thinking off use it.
        effort = effort if cell.thinking else "none"
        if effort:
            body["reasoning_effort"] = effort
    else:
        body["max_tokens"] = max_tokens
        if not cell.thinking:
            body["thinking"] = {"type": "disabled"}
    if m == "json_mode":
        body["response_format"] = {"type": "json_object"}
    elif m == "strict" and provider == "openai":
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "nda_fields", "strict": True, "schema": schema}}
    elif m in ("tool", "strict"):
        fn = {"name": TOOL_NAME, "description": TOOL_DESC, "parameters": schema}
        if m == "strict":
            fn["strict"] = True
        body["tools"] = [{"type": "function", "function": fn}]
        forced = not tool_is_optional(provider, cell)
        body["tool_choice"] = {"type": "function", "function": {"name": TOOL_NAME}} if forced else "auto"
    return body


def parse(provider: str, cell: Cell, data: dict) -> dict:
    """API response -> {'text', 'obj', 'finish', 'tool_called', 'usage'}. 'obj' is set only when the API
    hands over an already parsed tool input (Anthropic); 'tool_called' is None when no tool was asked for."""
    if provider == "anthropic":
        blocks = data.get("content") or []
        text = "".join(b.get("text") or "" for b in blocks if b.get("type") == "text")
        obj, called = None, None
        if cell.mechanism == "tool":
            called = False
            for b in blocks:
                if b.get("type") == "tool_use" and b.get("name") == TOOL_NAME:
                    obj, called = b.get("input"), True
                    break
        u = data.get("usage") or {}
        cache_read = u.get("cache_read_input_tokens", 0) or 0
        cache_write = u.get("cache_creation_input_tokens", 0) or 0
        usage = {"input": (u.get("input_tokens", 0) or 0) + cache_read + cache_write, "cached_input": cache_read,
                 "output": u.get("output_tokens", 0) or 0, "reasoning": 0}
        return {"text": text, "obj": obj, "finish": data.get("stop_reason") or "", "tool_called": called,
                "usage": usage}
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = choice.get("finish_reason") or ""
    if msg.get("refusal"):
        finish = "refusal"
    text, called = msg.get("content"), None
    if uses_tool(provider, cell):
        called = False
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            if fn.get("name") == TOOL_NAME:
                text, called = fn.get("arguments"), True
                break
    u = data.get("usage") or {}
    if provider == "deepseek":
        cached = u.get("prompt_cache_hit_tokens", 0) or 0
    else:
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    usage = {"input": u.get("prompt_tokens", 0) or 0, "cached_input": cached,
             "output": u.get("completion_tokens", 0) or 0,
             "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0}
    return {"text": text, "obj": None, "finish": finish, "tool_called": called, "usage": usage}

# ---------------------------------------------------------------- what went wrong with an answer


# What feedback says about a wrapped answer. The first version sent json.loads' own error ("Expecting value"),
# which does not say what is wrong; see reliability.md, changes after the two-document check.
WRAPPED = "the JSON object came with a code fence or other text around it; send the JSON object alone"
STOPPED = {"insufficient_system_resource", "aborted", "content_filter", "pause_turn"}
TRUNCATED = {"length", "max_tokens"}
CATEGORIES = ("ok", "wrapped", "invalid_json", "schema", "value", "truncated", "no_tool_call", "empty",
              "refusal", "stopped", "http_failed")


def check(obj) -> tuple:
    """bench.validate in two parts: what the JSON schema sent to the API already rules out ('schema'), and
    what only our value rules catch ('value': a date string that is not a real YYYY-MM-DD date, a term <= 0)."""
    problems = bench.validate(obj)
    value = []
    for p in problems:
        if p.startswith(("effective_date not YYYY-MM-DD", "effective_date is not a real date")):
            if isinstance(obj.get("effective_date"), str):
                value.append(p)
        elif p.startswith("term.number invalid"):
            n = obj["term"]["number"]
            if isinstance(n, (int, float)) and not isinstance(n, bool):
                value.append(p)
    return [p for p in problems if p not in value], value


_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n?(.*?)```", re.S)


def matching_brace(s: str, start: int):
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_json(text: str):
    """A JSON object inside a code fence or prose, or None. An object nested in a broken outer one is not
    taken for the answer."""
    for m in _FENCE.finditer(text):
        try:
            v = json.loads(m.group(1))
            if isinstance(v, (dict, list)):
                return v
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    while start != -1:
        end = matching_brace(text, start)
        if end is None:
            break
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            start = text.find("{", end + 1)
    return None


def close_truncated(text: str):
    """The longest prefix of a cut-off JSON object that parses once its open brackets are closed, or None."""
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]
    stack, in_str, esc = [], False, False
    state = [(False, "")]  # state[i]: (inside a string, closing brackets) for the prefix s[:i]
    for c in s:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]" and stack:
            stack.pop()
        state.append((in_str, "".join(reversed(stack))))
    for cut in range(len(s), 0, -1):
        inside, closers = state[cut]
        if inside:
            continue
        t = s[:cut].rstrip().rstrip(",").rstrip()
        if not t or t.endswith(":"):
            continue
        try:
            v = json.loads(t + closers)
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            return v
    return None


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm_key(k) -> str:
    k = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(k)).lower()
    return re.sub(r"[^a-z0-9]+", "_", k).strip("_")


def examine(resp: dict, cell: Cell) -> tuple:
    """A first answer as a plain pipeline sees it: json.loads on the text (or the tool input the API already
    parsed), then the schema, then the value rules. -> (category, problems, usable object or None)"""
    fin = resp.get("finish") or ""
    if fin in STOPPED:
        return "stopped", [f"finish_reason {fin}"], None
    if fin == "refusal":
        return "refusal", ["refusal"], None
    if fin in TRUNCATED:
        return "truncated", ["cut off at the output limit"], None
    obj = resp.get("obj")
    if obj is None:
        text = resp.get("text") or ""
        if resp.get("tool_called") is False:
            return "no_tool_call", ["no tool call" + (", answered in text" if text.strip() else "")], None
        if not text.strip():
            return "empty", ["empty output"], None
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            if extract_json(text) is not None:
                return "wrapped", [WRAPPED], None
            return "invalid_json", [f"invalid json: {e.msg}"], None
    if cell.variant == "nonull":
        obj = denull(obj)
    schema_p, value_p = check(obj)
    if schema_p:
        return "schema", schema_p + value_p, None
    if value_p:
        return "value", value_p, None
    return "ok", [], obj

# ---------------------------------------------------------------- local repair (no model call)


UNITS = {"day": "days", "days": "days", "week": "weeks", "weeks": "weeks", "wk": "weeks", "wks": "weeks",
         "month": "months", "months": "months", "mo": "months", "mos": "months", "mth": "months",
         "mths": "months", "year": "years", "years": "years", "yr": "years", "yrs": "years"}
_EMPTY_WORDS = {"", "null", "none", "n/a", "na", "not stated", "not specified", "unknown", "not applicable"}
_MONTHS = "|".join(bench.MONTHS)
_NUMBER = r"(\d+(?:\.\d+)?|" + "|".join(sorted(bench._NUM_WORDS, key=len, reverse=True)) + r")"


def _number(tok: str) -> float:
    return float(bench._NUM_WORDS[tok]) if tok in bench._NUM_WORDS else float(tok)


def read_date(v):
    """-> (ISO date or None, note). Only forms with one possible reading are read; the rest are dropped."""
    if v is None:
        return None, ""
    if not isinstance(v, str):
        return None, "dropped"
    s = v.strip()
    if s.lower() in _EMPTY_WORDS:
        return None, ("read as not stated" if s else "")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$", s)
    if m:
        try:
            iso = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            return iso, ("" if iso == s else "date reformatted")
        except ValueError:
            return None, "dropped"
    def month(name):
        return bench.MONTHS.index(name.lower()) + 1

    ymd = None
    m1 = re.match(rf"^(?:[a-z]+,?\s+)?({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})$", s, re.I)
    m2 = re.match(rf"^(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?({_MONTHS}),?\s+(\d{{4}})$", s, re.I)
    m3 = re.match(r"^(\d{1,2})[/.](\d{1,2})[/.](\d{4})$", s)
    if m1:
        ymd = (int(m1.group(3)), month(m1.group(1)), int(m1.group(2)))
    elif m2:
        ymd = (int(m2.group(3)), month(m2.group(2)), int(m2.group(1)))
    elif m3:
        a, b, y = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        if a == b or (a <= 12 < b):
            ymd = (y, a, b)              # month first, or the same either way
        elif b <= 12 < a:
            ymd = (y, b, a)              # day first
    if ymd:
        try:
            return dt.date(*ymd).isoformat(), "date reformatted"
        except ValueError:
            return None, "dropped"
    return None, "dropped"               # a bare year, a month and year, a number, words


def read_term(v):
    """-> (term object or None, note)."""
    if v is None:
        return None, ""
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _EMPTY_WORDS:
            return None, ("read as not stated" if s else "")
        m = re.search(r"\b" + _NUMBER + r"\s*(?:\(\d+\)\s*)?-?\s*(day|week|wk|month|mo|mth|year|yr)s?\b", s)
        if m and _number(m.group(1)) > 0:
            n = _number(m.group(1))
            return {"number": int(n) if n.is_integer() else n, "unit": UNITS[m.group(2)]}, "term read from text"
        return None, "dropped"
    if isinstance(v, dict):
        n = v.get("number", v.get("value"))
        u = v.get("unit", v.get("units"))
        if isinstance(n, str):
            try:
                n = _number(n.strip().lower())
            except (ValueError, KeyError):
                return None, "dropped"
        if isinstance(n, bool) or not isinstance(n, (int, float)) or n <= 0:
            return None, "dropped"
        unit = UNITS.get(str(u).strip().lower().rstrip(".")) if u is not None else None
        if unit is None:
            return None, "dropped"
        n = int(n) if float(n).is_integer() else n
        note = "" if (set(v) == {"number", "unit"} and unit == u and n == v["number"]) else "term reshaped"
        return {"number": n, "unit": unit}, note
    return None, "dropped"


KEY_ALIASES = {"party": "parties", "date": "effective_date", "governing_law": "jurisdiction", "duration": "term"}


def repair(resp: dict, cell=None, category: str = "") -> tuple:
    """Local repair of one answer, without calling the model. -> (object or None, repairs, dropped fields).

    It reshapes what the model wrote and never invents a value: a value it cannot read becomes empty and its
    field is listed as dropped, so a caller can tell a complete answer from a partial one. `category` is what
    examine() said about the answer: after a cut-off or stopped answer a missing key is unknown, not null."""
    repairs, dropped = [], []
    obj = resp.get("obj")
    cut_off = category in ("truncated", "stopped")
    if obj is None:
        text = (resp.get("text") or "").strip()
        if not text:
            return None, repairs, dropped
        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        steps = ((lambda: json.loads(text), None),
                 (lambda: json.loads(fixed), "trailing comma removed"),
                 (lambda: extract_json(text), "json taken out of prose or a code fence"),
                 (lambda: extract_json(fixed), "json taken out of prose; trailing comma removed"),
                 (lambda: close_truncated(fixed), "cut-off json closed"))
        for step, note in steps:
            try:
                obj = step()
            except json.JSONDecodeError:
                continue
            if obj is not None:
                if note:
                    repairs.append(note)
                cut_off = cut_off or note == "cut-off json closed"
                break
        if obj is None:
            return None, repairs, dropped
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        obj = obj[0]
        repairs.append("object taken out of a list")
    if not isinstance(obj, dict):
        return None, repairs, dropped
    if cell is not None and cell.variant == "nonull":
        obj = denull(obj)
    wanted = list(SCHEMA["required"])
    given = {norm_key(k) for k in obj}
    keyed, unknown_keys = {}, False
    for k, v in obj.items():
        nk = norm_key(k)
        if nk not in wanted:
            near = [w for w in wanted if w not in given and edit_distance(nk, w) <= 2]
            if KEY_ALIASES.get(nk) in wanted and KEY_ALIASES[nk] not in given:
                near = [KEY_ALIASES[nk]]
            if len(near) != 1:
                repairs.append(f"key {k!r} dropped")
                unknown_keys = True
                continue
            repairs.append(f"key {k!r} read as {near[0]!r}")
            nk = near[0]
        elif nk != k:
            repairs.append(f"key {k!r} read as {nk!r}")
        keyed.setdefault(nk, v)
    missing = [k for k in wanted if k not in keyed]
    for k in missing:
        # Unknown if the answer was cut off, if a key we could not read may have held it, or if it is the party
        # list (the prompt asks for a list, possibly empty). Otherwise a left-out key is a common way to say null.
        if cut_off or unknown_keys or k == "parties":
            dropped.append(k)
        else:
            repairs.append(f"missing {k} read as not stated")
    out = {}
    p = keyed.get("parties")
    if isinstance(p, str):
        p = [p]
        repairs.append("parties given as a string")
    if p is None:
        out["parties"] = []
    elif isinstance(p, list):
        names = []
        for x in p:
            if isinstance(x, dict) and isinstance(x.get("name"), str):
                x = x["name"]
            if isinstance(x, str) and x.strip():
                names.append(x.strip())
        if len(names) != len(p):
            repairs.append("parties reshaped")
        out["parties"] = names
    else:
        out["parties"] = []
        dropped.append("parties")
    for field, reader in (("effective_date", read_date), ("term", read_term)):
        val, note = reader(keyed.get(field))
        out[field] = val
        if note == "dropped":
            dropped.append(field)
        elif note:
            repairs.append(note)
    j = keyed.get("jurisdiction")
    if j is None or (isinstance(j, str) and j.strip().lower() in _EMPTY_WORDS):
        out["jurisdiction"] = None
    elif isinstance(j, str):
        out["jurisdiction"] = j.strip()
    else:
        out["jurisdiction"] = None
        dropped.append("jurisdiction")
    if bench.validate(out):
        return None, repairs, dropped
    if not (out["parties"] or out["effective_date"] or out["jurisdiction"] or out["term"]) and (dropped or missing):
        return None, repairs, sorted(set(dropped))      # nothing was salvaged
    return out, repairs, sorted(set(dropped))

# ---------------------------------------------------------------- transport


KEEP_HEADERS = ("retry-after", "retry-after-ms", "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
                "anthropic-ratelimit-requests-remaining", "anthropic-ratelimit-tokens-remaining",
                "anthropic-ratelimit-input-tokens-remaining", "anthropic-ratelimit-output-tokens-remaining")


def _lower(headers) -> dict:
    return {k.lower(): v for k, v in (headers.items() if headers else [])}


def post(url: str, headers: dict, body: dict, timeout: float, deadline: float = 0.0):
    """POST JSON -> (status, headers, raw, info); info: latency_s, headers_s, kind, keepalive_bytes.

    `timeout`, in urllib as in most HTTP clients, limits each wait for data, not the whole call: a server that
    sends a byte now and then keeps the call open as long as it likes. DeepSeek documents doing that while a
    request waits in its queue (empty lines, up to 10 minutes). `deadline` limits the whole call; it is
    checked between reads, so a call ends within about twice the deadline at worst."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "content-type": "application/json"})
    t0 = time.monotonic()
    wait = min(timeout, deadline) if deadline else timeout
    info = {"headers_s": None, "keepalive_bytes": 0}

    def done(status, hdrs, raw, kind):
        info.update(latency_s=round(time.monotonic() - t0, 3), kind=kind)
        return status, hdrs, raw, info

    try:
        r = urllib.request.urlopen(req, timeout=wait)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return done(e.code, _lower(e.headers), raw, "http")
    except (socket.timeout, TimeoutError) as e:
        return done(None, {}, str(e).encode(), "timeout")
    except urllib.error.URLError as e:
        kind = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network"
        return done(None, {}, str(e.reason).encode(), kind)
    except (ConnectionError, http.client.HTTPException, OSError) as e:
        return done(None, {}, repr(e).encode(), "network")
    info["headers_s"] = round(time.monotonic() - t0, 3)
    hdrs = _lower(r.headers)
    chunks = []
    try:
        with r:
            while True:
                if deadline and time.monotonic() - t0 > deadline:
                    return done(None, hdrs, b"".join(chunks), "deadline")
                chunk = r.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except (socket.timeout, TimeoutError):
        late = deadline and time.monotonic() - t0 >= deadline
        return done(None, hdrs, b"".join(chunks), "deadline" if late else "timeout")
    except (ConnectionError, http.client.HTTPException, OSError) as e:
        return done(None, hdrs, repr(e).encode(), "network")
    raw = b"".join(chunks)
    info["keepalive_bytes"] = len(raw) - len(raw.lstrip())
    return done(r.status, hdrs, raw, "http")


def call(url: str, headers: dict, body: dict, cfg, state: RunState, attempts: list):
    """-> (response JSON, start time) or None. Retries 408/409/425/429/5xx, timeouts and dropped connections
    with exponential backoff and jitter, waiting at least Retry-After; 400 fails the answer; 401/403/404 raise
    Fatal. A request that timed out is sent once more at most: a provider may bill a request the client gave
    up on, so repeating a slow one five times could pay for it five times. Every attempt is appended to
    `attempts`."""
    slow = 0
    for n in range(cfg.max_attempts):
        if state.stop_reason:
            return None
        started = bench.utcnow()
        status, rh, raw, info = post(url, headers, body, cfg.timeout, cfg.deadline)
        rec = {"at": started.isoformat(timespec="seconds"), "status": status, **info}
        kept = {k: rh[k] for k in KEEP_HEADERS if k in rh}
        if kept:
            rec["headers"] = kept
        attempts.append(rec)
        if status == 200:
            try:
                return json.loads(raw), started
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                rec["kind"] = "bad response body"
                rec["detail"] = f"{e!r}: {raw[:120]!r}"
        elif status in (401, 403):
            raise Fatal(f"HTTP {status} (check the API key): {raw[:300]!r}")
        elif status == 402:
            raise Fatal(f"HTTP 402, balance too low: {raw[:300]!r}")
        elif status == 404:
            raise Fatal(f"HTTP 404, unknown model or endpoint: {raw[:300]!r}")
        elif status is not None and status not in bench.RETRYABLE_STATUS:
            rec["detail"] = raw[:500].decode("utf-8", "replace")
            return None
        else:
            rec["detail"] = raw[:500].decode("utf-8", "replace")
        if info["kind"] in ("timeout", "deadline"):
            slow += 1
        if n + 1 == cfg.max_attempts or slow >= 2:
            break
        delay = min(cfg.backoff_cap, cfg.backoff_base * (2 ** n))
        delay = delay / 2 + random.uniform(0, delay / 2)
        ra = rh.get("retry-after")
        if ra:
            try:
                delay = max(delay, min(float(ra), cfg.backoff_cap))
            except ValueError:
                pass
        rec["retry_in_s"] = round(delay, 2)
        time.sleep(delay)
    return None


ZERO_USAGE = {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0}


def no_response(ans: dict) -> bool:
    """A failure without any HTTP answer (timeouts, dropped connections): what an outage looks like. A 429 or
    a 5xx is an answer, so a provider that limits a burst is not unreachable."""
    return (bool(ans) and ans.get("category") == "http_failed" and bool(ans.get("attempts"))
            and ans["attempts"][-1].get("status") is None)


def note_reachability(provider: str, ans: dict, cfg, state: RunState) -> None:
    """Called after an answer is saved. Too many answers in a row without any HTTP response stop the
    provider for the rest of the run; nothing already answered is lost."""
    with state.lock:
        if not hasattr(state, "unreachable"):
            state.unreachable = collections.Counter()
        state.unreachable[provider] = state.unreachable[provider] + 1 if no_response(ans) else 0
        n = state.unreachable[provider]
    if n >= cfg.max_failed_in_row:
        raise Fatal(f"{provider} unreachable: {n} requests in a row got no response ({ans['problems'][0][:120]}). "
                    "Finished documents are kept; run the same command again to continue.")


def transport_failure(ans: dict) -> bool:
    """An answer that failed on the way (no connection, a timeout, 429/5xx to the end), not one the API
    rejected: worth sending again on the next run."""
    if not ans or ans.get("category") != "http_failed" or not ans.get("attempts"):
        return False
    status = ans["attempts"][-1].get("status")
    return status is None or status in bench.RETRYABLE_STATUS or status == 200


def one_answer(cell: Cell, provider: str, price: dict, text: str, cfg, state: RunState, max_tokens: int,
               extra=()):
    """One answer from the model, HTTP retries included. None if the budget stopped it before any request."""
    url, headers = endpoint(provider, cell)
    body = build(cell, provider, text, max_tokens, extra, cfg.reasoning_effort)
    t0 = time.monotonic()
    attempts = []
    got = call(url, headers, body, cfg, state, attempts)
    if not attempts or (got is None and state.stop_reason):
        return None                  # stopped by the budget: not an answer, sent again when the run resumes
    ans = {"attempts": attempts, "max_tokens": max_tokens}
    if got is None:
        last = attempts[-1]
        ans.update(category="http_failed", problems=[f"{last.get('kind')} {last.get('status')}: "
                                                     f"{last.get('detail', '')}"[:300]],
                   obj=None, usage=dict(ZERO_USAGE), cost_usd=0.0)
    else:
        data, started = got
        resp = parse(provider, cell, data)
        category, problems, obj = examine(resp, cell)
        usd = bench.cost_usd(price, resp["usage"], started)
        state.add_cost(usd)
        ans.update(category=category, problems=problems, obj=obj, resp=resp, raw=data, usage=resp["usage"],
                   cost_usd=usd, latency_s=attempts[-1]["latency_s"])
    ans["wall_s"] = round(time.monotonic() - t0, 3)
    return ans

# ---------------------------------------------------------------- records


def doc_path(cell: Cell, doc_id: str) -> Path:
    return RUNS_REL / cell.name / "docs" / f"{doc_id}.json"


def save(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


def load_cell(cell_name: str) -> list:
    return [json.loads(p.read_text()) for p in sorted((RUNS_REL / cell_name / "docs").glob("*.json"))]


def read_meta(cell_name: str) -> dict:
    p = RUNS_REL / cell_name / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def write_meta(cell: Cell, **kw) -> None:
    meta = read_meta(cell.name)
    meta.update({"cell": cell.name, "model": cell.model, "mechanism": cell.mechanism, "thinking": cell.thinking,
                 "burst": cell.burst, "repeat": cell.repeat, "variant": cell.variant}, **kw)
    p = RUNS_REL / cell.name / "meta.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=1))


def first_answer(doc: dict, cell: Cell, provider: str, price: dict, cfg, state: RunState):
    path = doc_path(cell, doc["id"])
    if path.exists():
        rec = json.loads(path.read_text())
        if rec.get("first") and not transport_failure(rec["first"]):
            return rec
    ans = one_answer(cell, provider, price, doc["text"], cfg, state, cfg.max_output_tokens)
    if ans is None:
        return None
    rec = {"doc_id": doc["id"], "cell": cell.name, "keys": doc["keys"], "first": ans, "recovery": {}}
    save(path, rec)
    note_reachability(provider, ans, cfg, state)
    return rec


def import_bench_cell(cell: Cell, provider: str, price: dict) -> int:
    """Copies the first answer (and bench's own second request, which is a plain retry) of each document of a
    bench.py run into runs-rel/<cell>/, where the recovery answers are added. The bench run is not changed."""
    src = bench_run_dir(cell) / "docs"
    if not src.exists():
        return 0
    meta = json.loads((bench_run_dir(cell) / "meta.json").read_text())
    n = 0
    for p in sorted(src.glob("*.json")):
        b = json.loads(p.read_text())
        dest = doc_path(cell, b["doc_id"])
        if dest.exists():
            n += 1
            continue
        # bench.py kept one list of HTTP attempts per document; each answer ends with a 200
        groups, cur = [], []
        for a in b.get("attempts", []):
            cur.append(dict(a, headers_s=None, keepalive_bytes=None))
            if a.get("status") == 200 and a.get("kind") != "bad response body":
                groups.append(cur)
                cur = []
        answers = []
        for i, o in enumerate(b.get("outputs", [])):
            raw = o.get("raw")
            if raw is None:
                continue
            resp = parse(provider, cell, raw)
            category, problems, obj = examine(resp, cell)
            atts = groups[i] if i < len(groups) else []
            at = atts[-1] if atts else {}
            when = dt.datetime.fromisoformat(at["at"]) if at.get("at") else bench.utcnow()
            answers.append({"attempts": atts, "max_tokens": meta.get("max_output_tokens", 4000),
                            "category": category, "problems": problems, "obj": obj, "resp": resp, "raw": raw,
                            "usage": resp["usage"], "cost_usd": bench.cost_usd(price, resp["usage"], when),
                            "latency_s": at.get("latency_s"), "wall_s": at.get("latency_s"), "source": "bench"})
        if answers:
            first = answers[0]
        else:
            first = {"attempts": b.get("attempts", []), "category": "http_failed", "problems": ["bench: failed"],
                     "obj": None, "usage": dict(ZERO_USAGE), "cost_usd": 0.0, "source": "bench"}
        rec = {"doc_id": b["doc_id"], "cell": cell.name, "keys": b["keys"], "first": first, "recovery": {}}
        if len(answers) > 1 and first["category"] != "ok":
            rec["recovery"]["retry"] = answers[1]
        save(dest, rec)
        n += 1
    started = meta.get("started")
    write_meta(cell, source="bench", workers=4, started=started)
    return n


def feedback_messages(provider: str, cell: Cell, first: dict) -> list:
    """The conversation after an unusable answer: the answer itself, then what was wrong with it."""
    raw = first.get("raw") or {}
    problems = "; ".join(first.get("problems") or [])[:500]
    if provider == "anthropic":
        blocks = raw.get("content") or []
        echo = {"role": "assistant", "content": blocks}
        uses = [b for b in blocks if b.get("type") == "tool_use"]
        if uses:
            return [echo, {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b.get("id"), "is_error": True,
                 "content": FEEDBACK_TOOL.format(problems=problems) if b.get("name") == TOOL_NAME else "ignored"}
                for b in uses]}]
        return [echo, {"role": "user", "content": FEEDBACK_TEXT.format(problems=problems)}]
    msg = ((raw.get("choices") or [{}])[0].get("message")) or {}
    echo = {"role": "assistant", "content": msg.get("content")}
    if provider == "deepseek" and msg.get("reasoning_content"):
        echo["reasoning_content"] = msg["reasoning_content"]  # DeepSeek answers 400 if it is left out
    calls = msg.get("tool_calls") or []
    if calls:
        echo["tool_calls"] = calls
        return [echo] + [{"role": "tool", "tool_call_id": c.get("id"),
                          "content": FEEDBACK_TOOL.format(problems=problems)
                          if (c.get("function") or {}).get("name") == TOOL_NAME else "ignored"} for c in calls]
    if echo["content"] is None:
        echo["content"] = ""
    if first.get("category") == "no_tool_call":
        return [echo, {"role": "user", "content": ASK_TOOL}]
    return [echo, {"role": "user", "content": FEEDBACK_TEXT.format(problems=problems)}]


FEEDBACK_ON = {"wrapped", "invalid_json", "schema", "value", "no_tool_call"}
RETRY_ON = FEEDBACK_ON | {"truncated", "empty", "stopped", "refusal"}


def recover_doc(doc: dict, cell: Cell, provider: str, price: dict, cfg, state: RunState) -> None:
    """Extra requests for a document whose first answer was unusable, one per policy that applies:
    the same request again; the answer and what was wrong with it; a larger output limit if it was cut off."""
    path = doc_path(cell, doc["id"])
    if not path.exists():
        return
    rec = json.loads(path.read_text())
    first = rec["first"]
    category = first["category"]
    have = {p for p, a in rec["recovery"].items() if not transport_failure(a)}
    todo = []
    if category in RETRY_ON and "retry" not in have:
        todo.append("retry")
    if category in FEEDBACK_ON and "feedback" not in have:
        todo.append("feedback")
    if category == "truncated" and "bigger" not in have:
        todo.append("bigger")
    for policy in todo:
        if state.stop_reason:
            return
        extra = feedback_messages(provider, cell, first) if policy == "feedback" else ()
        c, tokens = cfg, cfg.max_output_tokens
        if policy == "bigger":       # 4 times the output may take 4 times as long, and nothing arrives until the end
            f = cfg.bigger_factor
            c = argparse.Namespace(**{**vars(cfg), "timeout": cfg.timeout * f, "deadline": cfg.deadline * f})
            tokens *= f
        ans = one_answer(cell, provider, price, doc["text"], c, state, tokens, extra)
        if ans is None:
            return
        rec["recovery"][policy] = ans
        save(path, rec)
        note_reachability(provider, ans, cfg, state)

# ---------------------------------------------------------------- run


def token_stats(prices: dict) -> dict:
    """Mean input and output tokens per request of each bench.py run: the basis of the estimates."""
    out = {}
    for model in prices:
        d = bench.RUNS / f"{model}__{bench.PROMPT_VERSION}__{SPLIT}" / "docs"
        if not d.exists():
            continue
        ins, outs = [], []
        for p in d.glob("*.json"):
            for o in json.loads(p.read_text()).get("outputs", []):
                u = (o.get("raw") or {}).get("usage") or {}
                i = u.get("prompt_tokens", u.get("input_tokens"))
                if i is not None:
                    ins.append(i + (u.get("cache_read_input_tokens", 0) or 0))
                    outs.append(u.get("completion_tokens", u.get("output_tokens", 0)) or 0)
        if ins:
            out[model] = (sum(ins) / len(ins), sum(outs) / len(outs))
    return out


def estimate_cell(cell: Cell, docs: list, prices: dict, stats: dict) -> float:
    """At the full (peak) price, without cache discounts: an upper estimate."""
    price = prices[cell.model]
    if cell.model in stats:
        inp, out = stats[cell.model]
    else:
        inp = sum((len(SYSTEM_PROMPT) + len(d["text"]) + 40) / 3.8 for d in docs) / max(len(docs), 1)
        out = 900 if price["provider"] == "openai" else 250
    if cell.mechanism == "prompt":
        out *= 1.5          # prose and code fences around the JSON
    if not cell.thinking:
        out = min(out, 200)
    return len(docs) * (inp * price["input"] + out * price["output"]) / 1e6


def select_cells(args, prices: dict) -> list:
    cells = [c for c in BASE_CELLS + (EXTENDED_CELLS if args.extended else []) if c.model in prices]
    if args.no_burst:
        cells = [c for c in cells if not (c.burst or c.repeat)]
    if args.cells:
        wanted = [w.strip() for w in args.cells.split(",") if w.strip()]
        cells = [c for c in cells if any(w in c.name for w in wanted)]
    return cells


def failure_share(cell: Cell) -> float:
    """Share of unusable first answers among the documents a cell has answered so far (None if none)."""
    d = RUNS_REL / cell.name / "docs"
    recs = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.exists() else []
    if not recs:
        return None
    return sum(1 for r in recs if r["first"]["category"] != "ok") / len(recs)


def cmd_plan(args) -> None:
    """Cells and an upper cost estimate. Recovery: about two requests (retry and feedback) per unusable first
    answer, at the share of unusable answers seen so far in the cell, and at least 10%."""
    docs = bench.load_split(SPLIT)
    if args.limit:
        docs = docs[:args.limit]
    prices = bench.load_prices()
    stats = token_stats(prices)
    cells = select_cells(args, prices)
    total = extra = 0.0
    print(f"{'cell':44s} {'docs':>5s} {'est. $':>8s}")
    for c in cells:
        est = estimate_cell(c, docs, prices, stats)
        total += est
        if not (c.burst or c.repeat):
            share = failure_share(c)
            extra += est * 2 * max(0.10, share if share is not None else 0.0)
        note = "   all documents at once" if c.burst else ("   a bench.py run again" if c.repeat else "")
        print(f"{c.name:44s} {len(docs):5d} {est:8.3f}{note}")
    print(f"{'recovery requests':44s} {'':5s} {extra:8.3f}")
    print(f"{'total, upper estimate':44s} {'':5s} {total + extra:8.3f}")
    print(f"\nbench.py runs reused as cells: {', '.join(c.name for c in bench_cells(prices))}")
    return total + extra


def run_cell(cell: Cell, provider: str, price: dict, docs: list, cfg, state: RunState) -> None:
    meta = read_meta(cell.name)
    if meta.get("skipped"):
        print(f"{cell.name}: skipped earlier ({meta['skipped'][:120]})")
        if meta.get("fallback"):
            run_cell(cell.with_variant(meta["fallback"]), provider, price, docs, cfg, state)
        return
    if not (cell.burst or cell.repeat):          # the repeats use a mechanism bench.py already ran
        smoke = [first_answer(d, cell, provider, price, cfg, state) for d in docs[:2]]
        rejected = [r for r in smoke if r and r["first"]["category"] == "http_failed"
                    and not transport_failure(r["first"])]        # 400, 422 and the like: the request itself
        if smoke and len(rejected) == len(smoke):
            reason = rejected[0]["first"]["problems"][0]
            fallback = "nonull" if (provider == "deepseek" and cell.mechanism == "strict" and not cell.variant) else ""
            write_meta(cell, skipped=reason, fallback=fallback)
            print(f"{cell.name}: the API rejected the request: {reason[:200]}")
            if fallback:
                print(f"{cell.name}: trying the schema without null ({fallback})")
                run_cell(cell.with_variant(fallback), provider, price, docs, cfg, state)
            return
    def pending(d):
        path = doc_path(cell, d["id"])
        if not path.exists():
            return True
        # a failure on the way is sent again on resume, except in a burst cell, where it is the result
        return not cell.burst and transport_failure(json.loads(path.read_text())["first"])

    todo = [d for d in docs if pending(d)]
    if todo:
        workers = len(todo) if cell.burst else max(1, cfg.workers)
        started = bench.utcnow()
        print(f"{cell.name}: {len(todo)} documents, {workers} at a time, spent so far ${state.spent:.3f}",
              flush=True)
        try:
            in_pool(workers, lambda d: first_answer(d, cell, provider, price, cfg, state), todo)
        finally:
            passes = read_meta(cell.name).get("passes", [])
            passes.append({"documents": len(todo), "workers": workers, "started": started.isoformat(timespec="seconds"),
                           "wall_s": round((bench.utcnow() - started).total_seconds(), 1)})
            main_pass = max(passes, key=lambda x: x["documents"])
            write_meta(cell, passes=passes, workers=main_pass["workers"], wall_s=main_pass["wall_s"])
    recs = [json.loads(doc_path(cell, d["id"]).read_text()) for d in docs if doc_path(cell, d["id"]).exists()]
    counts = collections.Counter(r["first"]["category"] for r in recs)
    print(f"  {cell.name}: {len(recs)}/{len(docs)} answered: "
          + ", ".join(f"{k} {v}" for k, v in counts.most_common()))


def recover_cell(cell: Cell, provider: str, price: dict, docs: list, cfg, state: RunState) -> None:
    """Recovery requests for one cell. The repeat and burst runs get none: they repeat bench.py requests to
    measure load and run-to-run change, and their failures would count the same requests twice."""
    if cell.repeat or cell.burst:
        return
    meta = read_meta(cell.name)
    if meta.get("skipped"):
        if meta.get("fallback"):
            recover_cell(cell.with_variant(meta["fallback"]), provider, price, docs, cfg, state)
        return
    todo = [d for d in docs if doc_path(cell, d["id"]).exists()]
    in_pool(cfg.workers, lambda d: recover_doc(d, cell, provider, price, cfg, state), todo)


def in_pool(workers: int, fn, items: list) -> None:
    """fn over items, `workers` at a time. The first Fatal (a bad key, an empty balance, an unknown model)
    stops the items not yet started and is raised once the pool is done."""
    fatal, errors = [], []

    def work(item):
        if fatal:
            return
        try:
            fn(item)
        except Fatal as e:
            fatal.append(e)
        except Exception as e:           # a bug or a surprise in one answer: keep it, go on with the rest
            errors.append(e)
            if len(errors) == 1:
                import traceback
                traceback.print_exc()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(work, items))
    if errors:
        print(f"  {len(errors)} document(s) raised an error (first one above); the others are kept")
    if fatal:
        raise fatal[0]


def cmd_run(args) -> None:
    docs = bench.load_split(SPLIT)
    if args.limit:
        docs = docs[:args.limit]
    prices = bench.load_prices()
    if args.ask_keys:
        bench.ask_keys()
    cells = select_cells(args, prices)
    est = cmd_plan(args)
    if args.max_usd and est > args.max_usd:
        sys.exit(f"the estimate ${est:.2f} is above --max-usd {args.max_usd}: raise it or choose fewer cells")
    print(f"budget: ${args.max_usd} for everything below\n")
    state = RunState(args.max_usd)
    refused = set()

    def has_key(provider):
        return bool(os.environ.get(KEY_ENV[provider])) and provider not in refused

    def guarded(fn, cell):
        provider = provider_of(cell.model, prices)
        if not has_key(provider):
            print(f"skip {cell.name}: no {KEY_ENV[provider]}")
            return
        try:
            fn(cell, provider, prices[cell.model], docs, args, state)
        except Fatal as e:
            print(f"skip {cell.name}: {e}")
            if any(f"HTTP {code}" in str(e) for code in (401, 402, 403)) or "unreachable" in str(e):
                refused.add(provider)

    for cell in cells:
        if state.stop_reason:
            break
        guarded(run_cell, cell)
    wanted_models = {c.model for c in cells}
    reused = [c for c in bench_cells(prices) if c.model in wanted_models or not args.cells]
    for cell in reused:
        import_bench_cell(cell, provider_of(cell.model, prices), prices[cell.model])
    print("\nrecovery requests for unusable first answers", flush=True)
    for cell in reused + cells:
        if state.stop_reason:
            break
        guarded(recover_cell, cell)
    if state.stop_reason:
        print(f"stopped early: {state.stop_reason}")
    print(f"spent ${state.spent:.3f}\n")
    cmd_report(args)

# ---------------------------------------------------------------- report


POLICIES = ("none", "local", "retry", "feedback", "bigger", "stack")
POLICY_TEXT = {
    "none": "json.loads and the checks, nothing else",
    "local": "local repair only, no request",
    "retry": "the same request once more",
    "feedback": "one more request with the answer and what was wrong with it",
    "bigger": "one more request with a larger output limit (cut-off answers only)",
    "stack": "local repair; if still incomplete, one feedback request (larger limit if cut off), repaired too",
}


def cell_from_name(name: str) -> Cell:
    parts = name.split("__")
    mech, _, variant = parts[1].partition("-")
    return Cell(parts[0], mech, thinking="nothink" not in parts[2:], burst="burst" in parts[2:], variant=variant,
                repeat="repeat" in parts[2:])


def outcomes(rec: dict, cell: Cell) -> dict:
    """Per policy: (object or None, dropped fields, extra requests, extra $, extra seconds), or None when the
    policy was not tried for this document."""
    first, more = rec["first"], rec.get("recovery", {})
    if first["category"] == "ok":
        return {p: (first["obj"], [], 0, 0.0, 0.0) for p in POLICIES}
    out = {"none": (None, [], 0, 0.0, 0.0)}
    local = repair(first["resp"], cell, first["category"]) if first.get("resp") else (None, [], [])
    out["local"] = (local[0], local[2] if local[0] is not None else [], 0, 0.0, 0.0)
    for p in ("retry", "feedback", "bigger"):
        a = more.get(p)
        out[p] = None if a is None else (a["obj"] if a["category"] == "ok" else None, [], 1,
                                         a.get("cost_usd", 0.0), a.get("wall_s") or 0.0)
    if local[0] is not None and not local[2]:
        out["stack"] = (local[0], [], 0, 0.0, 0.0)
        return out
    if first["category"] == "truncated":
        extra = more.get("bigger")
    else:
        extra = more.get("feedback") or more.get("retry")
    if extra is None:
        out["stack"] = None              # the request it needs was not made
        return out
    candidates = [(local[0], local[2])] if local[0] is not None else []
    calls, cost, secs = 1, extra.get("cost_usd", 0.0), extra.get("wall_s") or 0.0
    second = repair(extra["resp"], cell, extra["category"]) if extra.get("resp") else (None, [], [])
    if second[0] is not None and not second[2]:
        out["stack"] = (second[0], [], calls, cost, secs)
        return out
    if second[0] is not None:
        candidates.append((second[0], second[2]))
    best = min(candidates, key=lambda c: len(c[1])) if candidates else (None, [])
    out["stack"] = (best[0], best[1], calls, cost, secs)
    return out


def labels_of(obj, keys) -> set:
    return bench.to_labels(obj, keys) if obj else set()


def micro_f1(pairs) -> float:
    """pairs: (predicted labels, gold labels) per document."""
    tp = npred = ngold = 0
    for p, g in pairs:
        tp += len(p & g)
        npred += len(p)
        ngold += len(g)
    return bench.f1(tp, npred, ngold)


def pct_of(xs, q):
    return bench.pct(xs, q) if xs else float("nan")


def fmt_s(x) -> str:
    return "—" if x is None or x != x else f"{x:.1f}"


def cell_order(names: list, prices: dict) -> list:
    rank = {"openai": 0, "deepseek": 1, "anthropic": 2}
    mech = {m: i for i, m in enumerate(MECHANISMS)}

    def key(n):
        c = cell_from_name(n)
        return (c.burst or c.repeat, rank.get(provider_of(c.model, prices), 9) if c.model in prices else 9,
                c.model, mech.get(c.mechanism, 9), not c.thinking, c.variant, c.repeat, c.burst)
    return sorted(names, key=key)


_IDS = ((re.compile(r"\borg-[A-Za-z0-9]+"), "org-…"), (re.compile(r"\breq_[A-Za-z0-9]+"), "req_…"),
        (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "…"))


def without_ids(text: str) -> str:
    """Account and request identifiers out of an API message before it goes into a report."""
    for pattern, repl in _IDS:
        text = pattern.sub(repl, text)
    return text


def problem_line(p: str) -> str:
    """A problem, grouped and on one line: quoted values become …; for a failed request, the API's message."""
    m = re.match(r"^(http|timeout|network|deadline|bad response body) (\S+): (.*)$", p, re.S)
    if m:
        return without_ids(f"{m.group(1)} {m.group(2)}: {api_message(m.group(3))}")[:110]
    return re.sub(r"'[^']*'|\"[^\"]*\"", "…", p)[:90]


def api_message(detail: str) -> str:
    """The error message out of an API error body, on one line, for a table cell."""
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"?', detail)   # the body may be cut off mid-message
    text = m.group(1).replace('\\"', '"').replace("\\'", "'") if m else detail
    return without_ids(re.sub(r"\s+", " ", text).replace("|", "/").strip())[:220]


def first_attempt_latency(ans: dict):
    ok = [a for a in ans.get("attempts", []) if a.get("status") == 200]
    return ok[-1]["latency_s"] if ok else None


def cmd_report(args) -> None:
    docs = bench.load_split(SPLIT)
    gold = {d["id"]: d["gold"] for d in docs}
    prices = bench.load_prices()
    for cell in bench_cells(prices):               # local files only: no request is made here
        if bench_run_dir(cell).exists() and not (RUNS_REL / cell.name / "docs").exists():
            import_bench_cell(cell, provider_of(cell.model, prices), prices[cell.model])
    names = [p.name for p in RUNS_REL.iterdir() if (p / "docs").exists()] if RUNS_REL.exists() else []
    skipped = {n: read_meta(n) for n in names if read_meta(n).get("skipped")}
    names = cell_order([n for n in names if n not in skipped], prices)
    runs = {n: load_cell(n) for n in names}
    runs = {n: r for n, r in runs.items() if r}
    order = {n: i for i, n in enumerate(runs)}
    limits = sorted({r["first"].get("max_tokens") for recs in runs.values() for r in recs
                     if r["first"].get("max_tokens")})
    out = ["# Structured output on a bad day: results", "",
           f"Generated {bench.utcnow().strftime('%Y-%m-%d %H:%M')} UTC by `python3 reliability.py report`. "
           f"Kleister NDA `{SPLIT}`, prompt {bench.PROMPT_VERSION}, output limit on the first request "
           f"{', '.join(f'{x:,}' for x in limits) or '—'} tokens. A cell is one model asked one way; cells without "
           "`burst` sent 4 requests at a time.", ""]

    # 1. first answers
    out += ["## 1. First answers", "",
            "What a pipeline that runs `json.loads` and the checks gets on the first request, per cell. "
            "`wrapped`: valid JSON inside prose or a code fence; `schema`: parses, but breaks the JSON schema the "
            "API was given; `value`: fits the schema, fails a value rule (a date that is not a real YYYY-MM-DD "
            "date, a term <= 0); `truncated`: cut off at the output limit; `no tool call`: the tool was optional "
            "and the model answered in text; `stopped`: the provider ended the answer (e.g. "
            "`insufficient_system_resource`). Usable and F1 are over all documents of the split: an unusable "
            "or missing answer counts as empty. Latency is comparable only within one run: bench.py cells ran on "
            "2026-09-30 in the morning (UTC), the others later. Cost: what was paid, and in brackets the same "
            "tokens at the list price without cache and off-peak discounts; OpenAI and DeepSeek cache a repeated "
            "prompt prefix on their own, so what a cell paid depends on what ran just before it.", "",
            "| cell | answered | ok | wrapped | invalid JSON | schema | value | truncated | no tool call | empty "
            "| refusal / stopped | HTTP failed | usable | F1 | p50 / p95 s | $ / 1000 docs paid (list) |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for n, recs in runs.items():
        c = collections.Counter(r["first"]["category"] for r in recs)
        lat = [x for x in (first_attempt_latency(r["first"]) for r in recs) if x is not None]
        cost = sum(r["first"].get("cost_usd", 0.0) for r in recs)
        price = prices.get(cell_from_name(n).model, {})
        listed = sum((r["first"]["usage"]["input"] * price.get("input", 0)
                      + r["first"]["usage"]["output"] * price.get("output", 0)) / 1e6 for r in recs)
        by_doc = {r["doc_id"]: r for r in recs}
        f1 = micro_f1((labels_of(by_doc[d["id"]]["first"]["obj"], d["keys"]) if d["id"] in by_doc else set(),
                       d["gold"]) for d in docs)
        out.append(f"| {n} | {len(recs)}/{len(docs)} | {c['ok']} | {c['wrapped']} | {c['invalid_json']} | "
                   f"{c['schema']} | {c['value']} | {c['truncated']} | {c['no_tool_call']} | {c['empty']} | "
                   f"{c['refusal'] + c['stopped']} | {c['http_failed']} | {c['ok'] / len(docs):.1%} | {f1:.3f} | "
                   f"{fmt_s(pct_of(lat, .5))} / {fmt_s(pct_of(lat, .95))} | ${cost / len(recs) * 1000:.2f} "
                   f"(${listed / len(recs) * 1000:.2f}) |")
    for n, meta in skipped.items():
        out.append(f"| {n} | — | rejected by the API: {api_message(meta['skipped'])} |" + " |" * 13)
    out.append("")

    # 2. problems seen
    probs = collections.Counter()
    for n, recs in runs.items():
        for r in recs:
            for p in r["first"].get("problems") or []:
                probs[(n, problem_line(p))] += 1
    if probs:
        out += ["### What the unusable first answers got wrong", "",
                "| cell | problem | answers |", "|---|---|---|"]
        for (n, p), k in sorted(probs.items(), key=lambda x: (order[x[0][0]], -x[1], x[0][1])):
            out.append(f"| {n} | {p.replace('|', '/')} | {k} |")
        out.append("")

    # 3. recovery per failure category
    pooled = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    failures = collections.Counter()
    per_cell, per_cell_all = {}, {}
    for n, recs in runs.items():
        cell = cell_from_name(n)
        if cell.repeat or cell.burst:
            continue                     # no recovery requests there: see recover_cell
        rows = []
        per_cell_all[n] = rows
        for r in recs:
            oc = outcomes(r, cell)
            rows.append((r, oc))
            cat = r["first"]["category"]
            if cat == "ok":
                continue
            failures[cat] += 1
            for p in POLICIES[1:]:
                o = oc.get(p)
                if o is None:
                    continue
                pooled[cat][p][0] += 1                       # tried
                if o[0] is not None and not o[1]:
                    pooled[cat][p][1] += 1                   # complete answer
                elif o[0] is not None:
                    pooled[cat][p][2] += 1                   # partial answer
        per_cell[n] = rows
    if failures:
        out += ["## 2. What gets an unusable answer back", "",
                "All cells pooled except the repeat and burst runs, by what was wrong with the first answer. Each "
                "entry: complete answers / documents the policy was tried on (+ partial answers: usable, with a "
                "field emptied because its value could not be read). Policies:", ""]
        out += [f"- `{p}`: {POLICY_TEXT[p]}." for p in POLICIES[1:]]
        out += ["", "| first answer | documents | local | retry | feedback | bigger | stack |",
                "|---|---|---|---|---|---|---|"]
        for cat in CATEGORIES[1:]:
            if not failures[cat]:
                continue
            cells_txt = []
            for p in POLICIES[1:]:
                tried, full, part = pooled[cat][p]
                cells_txt.append("—" if not tried else f"{full}/{tried}" + (f" (+{part})" if part else ""))
            out.append(f"| {cat} | {failures[cat]} | " + " | ".join(cells_txt) + " |")
        out.append("")

        out += ["### Per cell: usable answers, F1 and the price of recovery", "",
                "Usable: complete answers (+ partial). Extra: requests and dollars per 1000 documents on top of "
                "the first answers, and the 95th percentile of the added seconds over the documents that needed "
                "it.", "",
                "| cell | unusable first | none | local | stack | stack not tried | F1 none | F1 local | F1 stack "
                "| extra requests / 1000 | extra $ / 1000 | added s p95 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n, rows in per_cell.items():
            bad = sum(1 for r, _ in rows if r["first"]["category"] != "ok")
            if not bad:
                continue
            txt, f1s = [], []
            for p in ("none", "local", "stack"):
                full = sum(1 for _, oc in rows if oc[p] and oc[p][0] is not None and not oc[p][1])
                part = sum(1 for _, oc in rows if oc[p] and oc[p][0] is not None and oc[p][1])
                txt.append(f"{full}" + (f" (+{part})" if part else ""))
                f1s.append(micro_f1((labels_of(oc[p][0] if oc[p] else None, r["keys"]), gold[r["doc_id"]])
                                    for r, oc in rows))
            untried = sum(1 for _, oc in rows if oc["stack"] is None)
            stacked = [oc["stack"] for _, oc in rows if oc["stack"]]
            calls = sum(x[2] for x in stacked)
            usd = sum(x[3] for x in stacked)
            added = [x[4] for x in stacked if x[2]]
            out.append(f"| {n} | {bad} | " + " | ".join(txt) + f" | {untried or '—'} | "
                       + " | ".join(f"{x:.3f}" for x in f1s)
                       + f" | {calls / len(rows) * 1000:.0f} | ${usd / len(rows) * 1000:.3f} | "
                         f"{fmt_s(pct_of(added, .95))} |")
        out.append("")

        compare = []
        for n in per_cell_all:
            c = cell_from_name(n)
            if not is_bench_cell(c, prices):
                continue
            others = [m for m in per_cell_all if m != n and cell_from_name(m).model == c.model]
            if not others:
                continue
            labs = {}
            for m in [n] + others:
                labs[m] = {}
                for r, oc in per_cell_all[m]:
                    o = oc["stack"]
                    labs[m][r["doc_id"]] = labels_of(o[0] if o else None, r["keys"])
            counts = {m: [(len(labs[m].get(d["id"], set()) & d["gold"]), len(labs[m].get(d["id"], set())),
                           len(d["gold"])) for d in docs] for m in labs}
            boot = bench.bootstrap_f1(counts, len(docs), getattr(args, "resamples", 10000))
            f1_of = {m: bench.f1(sum(x[0] for x in counts[m]), sum(x[1] for x in counts[m]),
                                 sum(x[2] for x in counts[m])) for m in labs}
            for m in others:
                lo, hi = bench.interval([x - y for x, y in zip(boot[m], boot[n])])
                compare.append(f"| {m} | {n} | {f1_of[m]:.3f} | {f1_of[n]:.3f} | {f1_of[m] - f1_of[n]:+.3f} "
                               f"({lo:+.3f} to {hi:+.3f}) |")
        if compare:
            out += ["### Mechanisms after recovery", "",
                    "F1 over the whole split after the stack, each cell against the extraction run of the same model, "
                    "on the same resampled documents (paired bootstrap, 95%).", "",
                    "| cell | against | F1 | F1 against | difference |", "|---|---|---|---|---|"] + compare + [""]

        wrong = []
        for n, rows in per_cell.items():
            for r, oc in rows:
                if r["first"]["category"] == "ok":
                    continue
                for p in ("local", "retry", "feedback", "bigger", "stack"):
                    o = oc.get(p)
                    if o and o[0] is not None:
                        pred = labels_of(o[0], r["keys"])
                        g = gold[r["doc_id"]]
                        wrong.append((p, len(pred ^ g), len(g)))
        if wrong:
            out += ["### Are recovered answers right?", "",
                    "Label errors (missed plus extra labels) in the answers each policy recovered, next to the "
                    "average over all first answers that were usable as they came.", "",
                    "| policy | answers recovered | label errors per answer |", "|---|---|---|"]
            for p in ("local", "retry", "feedback", "bigger", "stack"):
                xs = [e for q, e, _ in wrong if q == p]
                if xs:
                    out.append(f"| {p} | {len(xs)} | {sum(xs) / len(xs):.2f} |")
            base = [len(labels_of(r["first"]["obj"], r["keys"]) ^ gold[r["doc_id"]])
                    for rows in per_cell.values() for r, _ in rows if r["first"]["category"] == "ok"]
            if base:
                out.append(f"| usable first answers | {len(base)} | {sum(base) / len(base):.2f} |")
            out.append("")

    # 3. output tokens and limits
    per_model = collections.defaultdict(lambda: [[], 0])
    for n, recs in runs.items():
        c = cell_from_name(n)
        prov = provider_of(c.model, prices)
        mode = {"deepseek": "thinking" if c.thinking else "no thinking",
                "openai": "reasoning low" if c.thinking else "reasoning none"}.get(prov, "")
        for r in recs:
            for ans in [r["first"]] + list(r.get("recovery", {}).values()):
                if not ans.get("resp"):
                    continue
                key = (c.model, mode, ans.get("max_tokens", 4000))
                if ans["category"] == "truncated":
                    per_model[key][1] += 1
                else:
                    per_model[key][0].append(ans["usage"]["output"])
    token_rows = []
    for n, recs in runs.items():
        ans = [r["first"] for r in recs if r["first"].get("resp")]
        if ans:
            outs = sorted(a["usage"]["output"] for a in ans)
            token_rows.append(f"| {n} | {len(ans)} | {sum(a['usage']['input'] for a in ans) / len(ans):,.0f} | "
                              f"{sum(outs) / len(outs):,.0f} | {pct_of(outs, .5):,.0f} | "
                              f"{sum(a['usage']['reasoning'] for a in ans) / len(ans):,.0f} |")
    if per_model:
        out += ["## 3. Output tokens and the output limit", "",
                "First answers per cell, as each API reported them.", "",
                "| cell | answers | input tokens, mean | output tokens, mean | output, median | reasoning, mean |",
                "|---|---|---|---|---|---|"] + token_rows + [""]
        out += ["Output tokens per answer (reasoning included), over answers that finished, and how many answers "
                "a lower limit would have cut off. Answers that hit the limit count as longer than any lower limit.",
                "",
                "| model | mode | limit | answers | cut off | p50 | p95 | p99 | max | ≥ 1000 | ≥ 2000 | ≥ 3000 |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for (model, mode, limit), (xs, cut) in sorted(per_model.items()):
            n_all = len(xs) + cut
            over = [(sum(1 for x in xs if x >= c) + (cut if c <= limit else 0)) / n_all for c in (1000, 2000, 3000)]
            q = [f"{pct_of(xs, x):.0f}" if xs else "—" for x in (.5, .95, .99)]
            out.append(f"| {model} | {mode or '—'} | {limit} | {n_all} | {cut} | {' | '.join(q)} | "
                       f"{max(xs) if xs else '—'} | " + " | ".join(f"{o:.1%}" for o in over) + " |")
        out.append("")

    # 4. the same requests again: 4 at a time, then all at once
    groups = []
    for n in runs:
        c = cell_from_name(n)
        if is_bench_cell(c, prices):
            again = (Cell(c.model, c.mechanism, repeat=True), Cell(c.model, c.mechanism, burst=True))
            reps = [x.name for x in again if x.name in runs]
            if reps:
                groups.append([n] + reps)
    if groups:
        out += ["## 4. All documents at once", "",
                "Each bench.py run (4 requests at a time, 2026-09-30 morning UTC) was repeated twice in a row: "
                "4 requests at a time (`repeat`), then every document at once (`burst`). Compare the burst with "
                "the repeat, which ran in the same hour. A burst cell counts only its first pass: `no record` is "
                "the number of documents of that pass without an answer on file. Latency is per successful "
                "request; `headers` is the time to the response headers; `keep-alive` counts answers whose body "
                "began with whitespace, the sign of a request held in a queue.", "",
                "| cell | at once | requests | 429 | 5xx | timeouts / dropped | Retry-After seen | answered "
                "| failed | no record | p50 / p95 / max s | headers p95 s | keep-alive | wall s |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for group in groups:
            for n in group:
                recs, meta = runs[n], read_meta(n)
                first_pass = (meta.get("passes") or [None])[0]
                expected = len(recs)
                if cell_from_name(n).burst and first_pass:
                    t0 = dt.datetime.fromisoformat(first_pass["started"])
                    t1 = t0 + dt.timedelta(seconds=first_pass["wall_s"] + 5)
                    recs = [r for r in recs if r["first"].get("attempts")
                            and dt.datetime.fromisoformat(r["first"]["attempts"][0]["at"]) <= t1]
                    expected = first_pass["documents"]
                att = [a for r in recs for a in r["first"].get("attempts", [])]
                st = collections.Counter(a.get("status") for a in att)
                lat = [a["latency_s"] for a in att if a.get("status") == 200]
                hs = [a["headers_s"] for a in att if a.get("headers_s") is not None]
                ra = sorted({a["headers"]["retry-after"] for a in att if "retry-after" in (a.get("headers") or {})})
                keep = sum(1 for a in att if (a.get("keepalive_bytes") or 0) > 0)
                five = sum(v for k, v in st.items() if k and k >= 500)
                drops = sum(1 for a in att if a.get("status") is None)
                failed = sum(1 for r in recs if r["first"]["category"] == "http_failed")
                wall = first_pass["wall_s"] if first_pass else bench_wall(recs)
                out.append(f"| {n} | {first_pass['workers'] if first_pass else meta.get('workers', 4)} | {len(att)} | "
                           f"{st.get(429, 0)} | {five} | {drops} | {', '.join(ra) or '—'} | {len(recs) - failed} | "
                           f"{failed} | {expected - len(recs)} | {fmt_s(pct_of(lat, .5))} / {fmt_s(pct_of(lat, .95))} / "
                           f"{fmt_s(max(lat) if lat else None)} | {fmt_s(pct_of(hs, .95)) if hs else '—'} | "
                           f"{keep if hs else '—'} | {fmt_s(wall)} |")
        out.append("")

        out += ["### The same requests, run again", "",
                "On the documents with a usable first answer in every run: how many came out with the same labels "
                "every time, F1 of each run on those documents, and each repeat minus the bench.py run with its "
                "paired 95% interval. The interval resamples documents; it does not know that the model may answer "
                "the same document differently next time.", "",
                "| model | documents | same labels in every run | F1 bench / repeat / burst | repeat − bench "
                "| burst − bench |", "|---|---|---|---|---|---|"]
        for group in groups:
            by = [{r["doc_id"]: r for r in runs[n]} for n in group]
            common = [d for d in docs if all(d["id"] in b and b[d["id"]]["first"]["category"] == "ok" for b in by)]
            if not common:
                continue
            preds = [{d["id"]: labels_of(b[d["id"]]["first"]["obj"], d["keys"]) for d in common} for b in by]
            same = sum(1 for d in common if all(p[d["id"]] == preds[0][d["id"]] for p in preds))
            counts = {i: [(len(p[d["id"]] & d["gold"]), len(p[d["id"]]), len(d["gold"])) for d in common]
                      for i, p in enumerate(preds)}
            f1s = [micro_f1((p[d["id"]], d["gold"]) for d in common) for p in preds]
            boot = bench.bootstrap_f1(counts, len(common), getattr(args, "resamples", 10000))
            diffs, shown = {"repeat": "—", "burst": "—"}, {"repeat": "—", "burst": "—"}
            for i in range(1, len(group)):
                kind = "burst" if cell_from_name(group[i]).burst else "repeat"
                lo, hi = bench.interval([x - y for x, y in zip(boot[i], boot[0])])
                diffs[kind] = f"{f1s[i] - f1s[0]:+.3f} ({lo:+.3f} to {hi:+.3f})"
                shown[kind] = f"{f1s[i]:.3f}"
            out.append(f"| {cell_from_name(group[0]).model} | {len(common)} | {same} | {f1s[0]:.3f} / "
                       f"{shown['repeat']} / {shown['burst']} | {diffs['repeat']} | {diffs['burst']} |")
        out.append("")

    # 5. deadlines
    out += ["## 5. What a deadline would cut", "",
            "Share of first answers that took longer than a deadline, and their share of the spend. A client that "
            "gives up at the deadline gets no answer; whether the provider still bills the request is not "
            "documented by these three APIs, so the last column is the spend at stake. bench.py cells ran on "
            "2026-09-30 in the morning (UTC), the other cells later: compare latency within one run.", "",
            "| cell | answers | > 5 s | > 10 s | > 30 s | > 60 s | spend on answers > 10 s |",
            "|---|---|---|---|---|---|---|"]
    for n, recs in runs.items():
        pts = [(first_attempt_latency(r["first"]), r["first"].get("cost_usd", 0.0)) for r in recs]
        pts = [p for p in pts if p[0] is not None]
        if not pts:
            continue
        spend = sum(c for _, c in pts) or 1.0
        shares = [sum(1 for s, _ in pts if s > d) / len(pts) for d in (5, 10, 30, 60)]
        slow = sum(c for s, c in pts if s > 10) / spend
        out.append(f"| {n} | {len(pts)} | " + " | ".join(f"{x:.1%}" for x in shares) + f" | {slow:.1%} |")
    out.append("")
    total = sum(r["first"].get("cost_usd", 0.0) + sum(a.get("cost_usd", 0.0) for a in r.get("recovery", {}).values())
                for n, recs in runs.items() if read_meta(n).get("source") != "bench" for r in recs)
    total += sum(a.get("cost_usd", 0.0) for n, recs in runs.items() if read_meta(n).get("source") == "bench"
                 for r in recs for a in r.get("recovery", {}).values() if a.get("source") != "bench")
    out += [f"New requests in runs-rel/ cost ${total:.2f} in total (bench.py runs not included).", ""]
    RESULTS.write_text("\n".join(out))
    if not getattr(args, "quiet", False):
        print("\n".join(out))


def bench_wall(recs: list):
    spans = []
    for r in recs:
        for a in r["first"].get("attempts", []):
            if a.get("at"):
                t = dt.datetime.fromisoformat(a["at"]).timestamp()
                spans.append((t, t + (a.get("latency_s") or 0)))
    if not spans:
        return None
    return max(e for _, e in spans) - min(s for s, _ in spans)

# ---------------------------------------------------------------- main


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def selection(p):
        p.add_argument("--cells", default="", help="comma-separated parts of cell names, e.g. luna,flash__strict")
        p.add_argument("--extended", action="store_true", help="also the prompt-only cells of the larger models")
        p.add_argument("--no-burst", dest="no_burst", action="store_true", help="leave out the burst cells")
        p.add_argument("--limit", type=int, default=0, help="first N documents only")

    p = sub.add_parser("plan")
    selection(p)
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("run")
    selection(p)
    p.add_argument("--ask-keys", dest="ask_keys", action="store_true", help="prompt for API keys (no echo)")
    p.add_argument("--max-usd", dest="max_usd", type=float, default=6.0, help="for the whole run; 0 = no cap")
    p.add_argument("--workers", type=int, default=4, help="requests at a time outside the burst cells")
    p.add_argument("--timeout", type=float, default=120.0, help="seconds of silence before a call is dropped")
    p.add_argument("--deadline", type=float, default=300.0, help="seconds for a whole call; 0 = none")
    p.add_argument("--max-attempts", dest="max_attempts", type=int, default=5, help="HTTP attempts per request")
    p.add_argument("--max-failed-in-row", dest="max_failed_in_row", type=int, default=6,
                   help="requests to one provider that fail on the way, in a row, before its cells are skipped")
    p.add_argument("--max-output-tokens", dest="max_output_tokens", type=int, default=4000)
    p.add_argument("--bigger-factor", dest="bigger_factor", type=int, default=4,
                   help="output limit multiplier for the retry of a cut-off answer")
    p.add_argument("--reasoning-effort", dest="reasoning_effort", default="low", help="OpenAI models, as bench.py")
    p.add_argument("--backoff-base", dest="backoff_base", type=float, default=2.0)
    p.add_argument("--backoff-cap", dest="backoff_cap", type=float, default=60.0)
    p.add_argument("--resamples", type=int, default=10000)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report")
    p.add_argument("--resamples", type=int, default=10000)
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        bench.load_key_file()
    args.fn(args)


if __name__ == "__main__":
    main()
