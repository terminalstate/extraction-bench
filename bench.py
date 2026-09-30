#!/usr/bin/env python3
"""Contract field extraction with LLMs, measured on a public benchmark (Kleister NDA).

Python 3.9+, standard library only. Commands:

  python3 bench.py fetch                     # download the dataset (pinned commit, sha256-checked)
  python3 bench.py estimate                  # expected cost of a run, per model
  python3 bench.py run --model rules         # offline regex baseline, no API key needed
  python3 bench.py run --model gpt-6-luna --limit 2
  python3 bench.py all                       # every model whose API key is set: 2-doc smoke test, then the split
  python3 bench.py report                    # results.md + errors/<run>.md

API keys are read from OPENAI_API_KEY, ANTHROPIC_API_KEY, DEEPSEEK_API_KEY.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import http.client
import json
import lzma
import math
import os
import random
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RUNS = ROOT / "runs"
PRICES = ROOT / "prices.json"

# ---------------------------------------------------------------- dataset

DATASET_REPO = "applicaai/kleister-nda"
DATASET_COMMIT = "c2c7bf069b919bfb618b268fda2a2c079c0db316"
DATASET_FILES = {
    "dev-0/in.tsv.xz": "728e6b4ce5f2347a596b9ed28f04fcee07b712cc12362155e489b2fe3e26eb48",
    "dev-0/expected.tsv": "2ac328d80ad15688a613d78d96384dca0f13aa99fad1952012a55a31052dca34",
    "train/in.tsv.xz": "f8bd7eb0f05829b470040e31164ca5735c22d758754b5fd59e272ac33a4eddd7",
    "train/expected.tsv": "db71adab8293add4380c4c87b6025e6f660c4fed772fda902a79e3f7547b35e9",
}
IN_COLUMNS = ["filename", "keys", "text_djvu", "text_tesseract", "text_textract", "text_best"]
FIELDS = ["party", "effective_date", "jurisdiction", "term"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_fetch(args) -> None:
    for rel, digest in DATASET_FILES.items():
        dest = DATA / rel
        if dest.exists() and sha256(dest) == digest:
            print(f"ok      {rel}")
            continue
        url = f"https://raw.githubusercontent.com/{DATASET_REPO}/{DATASET_COMMIT}/{rel}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body = r.read()
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                sys.exit("TLS certificate check failed. On macOS with python.org Python run "
                         "'/Applications/Python 3.x/Install Certificates.command' once, then retry.")
            raise
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(body)
        if sha256(tmp) != digest:
            tmp.unlink()
            sys.exit(f"sha256 mismatch for {rel}: refusing to use it")
        os.replace(tmp, dest)
        print(f"fetched {rel} ({len(body)} bytes)")


_ESC = {"n": "\n", "t": "\t", "f": "\n\f\n", "\\": "\\"}


def unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), "\\" + m.group(1)), s)


def load_split(split: str, text_col: str = "text_best") -> list:
    src = DATA / split / "in.tsv.xz"
    if not src.exists():
        sys.exit(f"{src} not found: run 'python3 bench.py fetch' first")
    csv.field_size_limit(2**31 - 1)
    col = IN_COLUMNS.index(text_col)
    with lzma.open(src, "rt", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    with open(DATA / split / "expected.tsv", encoding="utf-8") as f:
        expected = [line.rstrip("\n") for line in f]
    assert len(rows) == len(expected), "input and expected files are not aligned"
    docs = []
    for row, exp in zip(rows, expected):
        gold = set()
        for kv in exp.split():
            key, value = kv.split("=", 1)
            gold.add((key, value.upper()))
        docs.append({
            "id": row[0].rsplit(".", 1)[0],
            "keys": row[1].split(),
            "text": unescape(row[col]),
            "gold": gold,
        })
    return docs

# ---------------------------------------------------------------- task: prompt, schema, normalisation

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You extract key facts from the text of a non-disclosure agreement (NDA). The text was produced by OCR from a PDF, so expect broken lines, page headers and typos.

Fields:
- parties: the legal names of the parties that sign the agreement (companies or individuals), as written in the document. No role labels such as "the Company" or "Recipient", no addresses, no phrases like "and its subsidiaries". Usually two names.
- effective_date: the date the agreement takes effect, as YYYY-MM-DD: the date it says it is made, entered into, dated or effective as of. null if no such date is stated.
- jurisdiction: the US state or the country whose law governs the agreement (the governing-law clause), name only, for example "New York" or "Delaware". null if there is no governing-law clause.
- term: how long the agreement itself stays in force, as stated in the document: a number and a unit (days, weeks, months or years), keeping the document's own unit, so 24 months stays 24 months. Not the period confidentiality obligations survive after the agreement ends, and not a non-solicitation period. null if the agreement does not state its own duration.

Do not guess: if a field is not stated, use null (an empty list for parties).
Answer with one JSON object in exactly this shape:
{"parties": ["Name One", "Name Two"], "effective_date": "2014-05-20", "jurisdiction": "New York", "term": {"number": 2, "unit": "years"}}"""

USER_TEMPLATE = "Agreement text:\n<<<\n{text}\n>>>"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["parties", "effective_date", "jurisdiction", "term"],
    "properties": {
        "parties": {"type": "array", "items": {"type": "string"}},
        "effective_date": {"type": ["string", "null"]},
        "jurisdiction": {"type": ["string", "null"]},
        "term": {"anyOf": [
            {"type": "null"},
            {"type": "object", "additionalProperties": False, "required": ["number", "unit"],
             "properties": {"number": {"type": "number"},
                            "unit": {"type": "string", "enum": ["days", "weeks", "months", "years"]}}},
        ]},
    },
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate(obj) -> list:
    """Schema check done by us, whatever the provider promised. Returns a list of problems."""
    if not isinstance(obj, dict):
        return ["not an object"]
    errs = []
    missing = [k for k in SCHEMA["required"] if k not in obj]
    extra = [k for k in obj if k not in SCHEMA["properties"]]
    if missing:
        errs.append("missing " + ",".join(missing))
    if extra:
        errs.append("extra " + ",".join(extra))
    p = obj.get("parties")
    if not isinstance(p, list) or not all(isinstance(x, str) for x in p):
        errs.append("parties is not a list of strings")
    d = obj.get("effective_date")
    if d is not None:
        if not isinstance(d, str) or not _DATE.match(d):
            errs.append(f"effective_date not YYYY-MM-DD: {d!r}")
        else:
            try:
                dt.date.fromisoformat(d)
            except ValueError:
                errs.append(f"effective_date is not a real date: {d!r}")
    j = obj.get("jurisdiction")
    if j is not None and not isinstance(j, str):
        errs.append("jurisdiction is not a string")
    t = obj.get("term")
    if t is not None:
        if not isinstance(t, dict) or set(t) != {"number", "unit"}:
            errs.append(f"term has wrong shape: {t!r}")
        elif isinstance(t["number"], bool) or not isinstance(t["number"], (int, float)) or t["number"] <= 0:
            errs.append(f"term.number invalid: {t['number']!r}")
        elif t["unit"] not in ("days", "weeks", "months", "years"):
            errs.append(f"term.unit invalid: {t['unit']!r}")
    return errs


def norm_value(s: str) -> str:
    """Dataset normalisation (README of the dataset + what the train split shows): commas dropped,
    '&' written as 'and', spaces and colons become underscores; comparison is upper-cased."""
    s = re.sub(r"\s+", " ", s.replace(",", " ").replace("&", " and ")).strip()
    return s.replace(" ", "_").replace(":", "_").upper()


def to_labels(obj, keys) -> set:
    """Structured answer -> the dataset's key=value labels, only for keys the document asks for."""
    out = set()
    if not obj:
        return out
    if "party" in keys:
        for name in obj.get("parties") or []:
            if name and name.strip():
                out.add(("party", norm_value(name)))
    if "effective_date" in keys and obj.get("effective_date"):
        out.add(("effective_date", obj["effective_date"]))
    if "jurisdiction" in keys and obj.get("jurisdiction"):
        j = re.sub(r"(?i)^(the\s+)?(state|commonwealth)\s+of\s+", "", obj["jurisdiction"].strip())
        out.add(("jurisdiction", norm_value(j)))
    if "term" in keys and obj.get("term"):
        n = float(obj["term"]["number"])
        n_str = str(int(n)) if n.is_integer() else str(n)
        unit = obj["term"]["unit"].rstrip("s")
        out.add(("term", f"{n_str}_{unit}{'' if n == 1 else 's'}".upper()))
    return out

# ---------------------------------------------------------------- offline baseline

US_STATES = ["Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
             "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
             "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
             "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
             "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
             "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
             "West Virginia", "Wisconsin", "Wyoming"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
_NUM_WORDS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                                          "nine", "ten", "eleven", "twelve"])}


def rules_extract(text: str) -> dict:
    """A deliberately plain regex extractor: the number an LLM has to beat."""
    t = re.sub(r"\s+", " ", text)
    states = "|".join(sorted(US_STATES, key=len, reverse=True))
    jur = None
    m = re.search(rf"laws of (?:the )?(?:State of |Commonwealth of )?({states})\b", t, re.I)
    if m:
        jur = m.group(1)
    date = None
    m = re.search(r"(?:dated|made|entered into|effective)(?: as of| on| this)?[^.]{0,60}?"
                  r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", t, re.I)
    if m:
        try:
            date = dt.date(int(m.group(3)), MONTHS.index(m.group(1).lower()) + 1, int(m.group(2))).isoformat()
        except ValueError:
            date = None
    term = None
    m = re.search(r"(?:term of this agreement|this agreement shall (?:remain|continue)[^.]{0,40}?)[^.]{0,60}?"
                  r"(?:(\d+)|\b(" + "|".join(_NUM_WORDS) + r"))\b\s*(?:\(\d+\)\s*)?(year|month|day|week)s?", t, re.I)
    if m:
        n = int(m.group(1)) if m.group(1) else _NUM_WORDS[m.group(2).lower()]
        if n > 0:
            term = {"number": n, "unit": m.group(3).lower() + "s"}
    parties = []
    m = re.search(r"\bbetween\s+(.{3,150}?)\s+and\s+(.{3,150}?)(?:\s*\(|,|\.)", t, re.I)
    if m:
        for g in (m.group(1), m.group(2)):
            g = re.split(r"\s*\(|, a |, an ", g)[0].strip(" ,\"'“”")
            if g:
                parties.append(g)
    return {"parties": parties, "effective_date": date, "jurisdiction": jur, "term": term}

# ---------------------------------------------------------------- providers

class Fatal(Exception):
    """Stops the whole run: bad key, unknown model, and similar."""


RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529}


def http_post(url: str, headers: dict, body: dict, timeout: float):
    """Returns (status, headers, raw_bytes, latency_s, kind). kind is 'http', 'timeout' or 'network'."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "content-type": "application/json"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(), time.monotonic() - t0, "http"
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, dict(e.headers or {}), raw, time.monotonic() - t0, "http"
    except (socket.timeout, TimeoutError) as e:
        return None, {}, str(e).encode(), time.monotonic() - t0, "timeout"
    except urllib.error.URLError as e:
        kind = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network"
        return None, {}, str(e.reason).encode(), time.monotonic() - t0, kind
    except (ConnectionError, http.client.HTTPException, OSError) as e:
        return None, {}, repr(e).encode(), time.monotonic() - t0, "network"


class Provider:
    name = ""
    key_env = ""
    base_env = ""
    default_base = ""

    def __init__(self, model: str, cfg):
        self.model = model
        self.cfg = cfg
        self.key = os.environ.get(self.key_env, "")
        self.base = os.environ.get(self.base_env, self.default_base).rstrip("/")

    def request(self, text: str):
        raise NotImplementedError

    def parse(self, data: dict) -> dict:
        """-> {'text': str|None, 'obj': dict|None, 'finish': str, 'usage': {...}}"""
        raise NotImplementedError


class OpenAIChat(Provider):
    name, key_env, base_env, default_base = "openai", "OPENAI_API_KEY", "OPENAI_BASE_URL", "https://api.openai.com/v1"

    def response_format(self):
        return {"type": "json_schema", "json_schema": {"name": "nda_fields", "strict": True, "schema": SCHEMA}}

    def request(self, text):
        body = {"model": self.model,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": USER_TEMPLATE.format(text=text)}],
                "response_format": self.response_format()}
        body.update(self.extra_body())
        return f"{self.base}/chat/completions", {"authorization": f"Bearer {self.key}"}, body

    def extra_body(self):
        b = {"max_completion_tokens": self.cfg.max_output_tokens}
        if self.cfg.reasoning_effort:
            b["reasoning_effort"] = self.cfg.reasoning_effort
        return b

    def parse(self, data):
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        finish = choice.get("finish_reason") or ""
        if msg.get("refusal"):
            finish = "refusal"
        u = data.get("usage") or {}
        return {"text": msg.get("content"), "obj": None, "finish": finish, "usage": self.usage(u)}

    def usage(self, u):
        return {"input": u.get("prompt_tokens", 0),
                "cached_input": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0,
                "output": u.get("completion_tokens", 0),
                "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0}


class DeepSeekChat(OpenAIChat):
    name, key_env, base_env, default_base = "deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "https://api.deepseek.com"

    def response_format(self):
        return {"type": "json_object"}  # no strict schema mode: we validate ourselves

    def extra_body(self):
        return {"max_tokens": self.cfg.max_output_tokens}

    def usage(self, u):
        return {"input": u.get("prompt_tokens", 0),
                "cached_input": u.get("prompt_cache_hit_tokens", 0) or 0,
                "output": u.get("completion_tokens", 0),
                "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0}


class AnthropicMessages(Provider):
    name, key_env, base_env, default_base = "anthropic", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "https://api.anthropic.com"

    def request(self, text):
        body = {"model": self.model, "max_tokens": self.cfg.max_output_tokens, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": USER_TEMPLATE.format(text=text)}],
                "tools": [{"name": "record_fields", "description": "Record the fields extracted from the agreement.",
                           "input_schema": SCHEMA}],
                "tool_choice": {"type": "tool", "name": "record_fields"}}
        return (f"{self.base}/v1/messages",
                {"x-api-key": self.key, "anthropic-version": "2023-06-01"}, body)

    def parse(self, data):
        obj = None
        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "record_fields":
                obj = block.get("input")
        u = data.get("usage") or {}
        cache_read = u.get("cache_read_input_tokens", 0) or 0
        cache_write = u.get("cache_creation_input_tokens", 0) or 0
        return {"text": None, "obj": obj, "finish": data.get("stop_reason") or "",
                "usage": {"input": u.get("input_tokens", 0) + cache_read + cache_write, "cached_input": cache_read,
                          "output": u.get("output_tokens", 0), "reasoning": 0}}


PROVIDERS = {p.name: p for p in (OpenAIChat, DeepSeekChat, AnthropicMessages)}


def interpret(resp: dict):
    """Provider response -> (obj or None, problems, repairs). Nothing here trusts the provider."""
    repairs = []
    if resp["finish"] in ("length", "max_tokens"):
        return None, ["truncated"], repairs
    if resp["finish"] == "refusal":
        return None, ["refusal"], repairs
    obj = resp["obj"]
    if obj is None:
        text = (resp["text"] or "").strip()
        if not text:
            return None, ["empty output"], repairs
        fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
        if fenced:
            text = fenced.group(1)
            repairs.append("code fence stripped")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return None, [f"invalid json: {e.msg}"], repairs
    problems = validate(obj)
    return (obj if not problems else None), problems, repairs

# ---------------------------------------------------------------- run

def load_prices() -> dict:
    return json.loads(PRICES.read_text())["models"]


def is_deepseek_peak(when: dt.datetime) -> bool:
    return when.weekday() < 5 and (1 <= when.hour < 4 or 6 <= when.hour < 10)


def cost_usd(price: dict, usage: dict, when: dt.datetime) -> float:
    if not price:
        return 0.0
    factor = 1.0
    if "offpeak_factor" in price and not is_deepseek_peak(when):
        factor = price["offpeak_factor"]
    fresh = max(usage["input"] - usage["cached_input"], 0)
    return factor * (fresh * price["input"] + usage["cached_input"] * price.get("cached_input", price["input"])
                     + usage["output"] * price["output"]) / 1e6


class RunState:
    def __init__(self, max_usd: float):
        self.lock = threading.Lock()
        self.spent = 0.0
        self.max_usd = max_usd
        self.stop_reason = ""
        self.hard_failures = 0

    def add_cost(self, usd: float):
        with self.lock:
            self.spent += usd
            if self.max_usd and self.spent >= self.max_usd and not self.stop_reason:
                self.stop_reason = f"budget reached: ${self.spent:.4f} >= ${self.max_usd}"

    def stop(self, reason: str):
        with self.lock:
            if not self.stop_reason:
                self.stop_reason = reason


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def call_with_retries(provider: Provider, text: str, cfg, state: RunState, attempts: list):
    url, headers, body = provider.request(text)
    for n in range(cfg.max_attempts):
        if state.stop_reason:
            return None
        started = utcnow()
        status, rh, raw, latency, kind = http_post(url, headers, body, cfg.timeout)
        rec = {"at": started.isoformat(timespec="seconds"), "latency_s": round(latency, 3),
               "status": status, "kind": kind}
        attempts.append(rec)
        if status == 200:
            try:
                data = json.loads(raw)
                return provider.parse(data), started, data
            except (json.JSONDecodeError, AttributeError, IndexError, TypeError) as e:
                rec["kind"] = "bad response body"
                rec["detail"] = repr(e)[:200]
        elif status in (401, 403):
            raise Fatal(f"{provider.name}: HTTP {status} (check {provider.key_env}): {raw[:300]!r}")
        elif status == 404:
            raise Fatal(f"{provider.name}: HTTP 404, unknown model or endpoint: {raw[:300]!r}")
        elif status is not None and status not in RETRYABLE_STATUS:
            rec["detail"] = raw[:500].decode("utf-8", "replace")
            return None
        else:
            rec["detail"] = raw[:200].decode("utf-8", "replace")
        if n + 1 == cfg.max_attempts:
            break
        delay = min(cfg.backoff_cap, cfg.backoff_base * (2 ** n))
        delay = delay / 2 + random.uniform(0, delay / 2)
        ra = (rh.get("retry-after") or rh.get("Retry-After")) if rh else None
        if ra:
            try:
                delay = max(delay, min(float(ra), cfg.backoff_cap))
            except ValueError:
                pass
        rec["retry_in_s"] = round(delay, 2)
        time.sleep(delay)
    return None


def process_doc(doc: dict, provider, price: dict, cfg, state: RunState, out_dir: Path) -> dict:
    path = out_dir / "docs" / f"{doc['id']}.json"
    if path.exists():
        prev = json.loads(path.read_text())
        if prev["status"] in ("ok", "invalid"):
            prev["cached"] = True
            return prev
    rec = {"doc_id": doc["id"], "keys": doc["keys"], "model": cfg.model, "prompt": PROMPT_VERSION,
           "attempts": [], "outputs": [], "usage": {"input": 0, "cached_input": 0, "output": 0, "reasoning": 0},
           "cost_usd": 0.0}
    t0 = time.monotonic()
    obj, status = None, "failed"
    if provider is None:  # offline baseline
        obj, status = rules_extract(doc["text"]), "ok"
        rec["outputs"].append({"obj": obj, "problems": [], "repairs": []})
    else:
        for _ in range(cfg.output_tries):
            got = call_with_retries(provider, doc["text"], cfg, state, rec["attempts"])
            if got is None:
                status = "failed"
                break
            resp, when, raw = got
            usd = cost_usd(price, resp["usage"], when)
            state.add_cost(usd)
            rec["cost_usd"] += usd
            for k in rec["usage"]:
                rec["usage"][k] += resp["usage"].get(k, 0) or 0
            obj, problems, repairs = interpret(resp)
            rec["outputs"].append({"finish": resp["finish"], "problems": problems, "repairs": repairs,
                                   "obj": obj, "raw": raw})
            if obj is not None:
                status = "ok"
                break
            status = "invalid"
    rec["status"] = status
    rec["invalid_outputs"] = sum(1 for o in rec["outputs"] if o["problems"])
    rec["pred"] = sorted(to_labels(obj, doc["keys"])) if status == "ok" else []
    rec["wall_s"] = round(time.monotonic() - t0, 3)
    if status == "failed":
        with state.lock:
            state.hard_failures += 1
            if state.hard_failures >= cfg.max_failed_docs:
                state.stop_reason = state.stop_reason or f"{state.hard_failures} documents failed: stopping"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    return rec


def run_name(model: str, split: str, tag: str) -> str:
    return f"{model}__{PROMPT_VERSION}__{split}" + (f"__{tag}" if tag else "")


def estimate_usd(docs: list, price: dict, provider_name: str) -> float:
    if not price:
        return 0.0
    out = 900 if provider_name == "openai" else 250  # reasoning tokens are billed as output
    total_in = sum((len(SYSTEM_PROMPT) + len(d["text"]) + 40) / 3.8 for d in docs)
    return (total_in * price["input"] + len(docs) * out * price["output"]) / 1e6


def run_model(cfg, docs: list) -> dict:
    prices = load_prices()
    if cfg.model == "rules":
        provider, price = None, {}
    else:
        if cfg.model not in prices:
            raise Fatal(f"model {cfg.model!r} is not in prices.json")
        price = prices[cfg.model]
        provider = PROVIDERS[price["provider"]](cfg.model, cfg)
        if not provider.key:
            raise Fatal(f"{provider.key_env} is not set")
    if cfg.limit:
        docs = docs[:cfg.limit]
    out_dir = RUNS / run_name(cfg.model, cfg.split, cfg.tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    est = estimate_usd(docs, price, price.get("provider", ""))
    print(f"{cfg.model}: {len(docs)} docs, estimated ${est:.3f}, budget ${cfg.max_usd}")
    if cfg.max_usd and est > cfg.max_usd:
        raise Fatal(f"estimate ${est:.2f} is above --max-usd {cfg.max_usd}; raise it or use --limit")
    meta = {"model": cfg.model, "split": cfg.split, "prompt": PROMPT_VERSION, "text_col": cfg.text_col,
            "reasoning_effort": cfg.reasoning_effort if price.get("provider") == "openai" else None,
            "timeout_s": cfg.timeout, "max_attempts": cfg.max_attempts, "output_tries": cfg.output_tries,
            "price": price, "started": utcnow().isoformat(timespec="seconds")}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    state = RunState(cfg.max_usd)
    results, done = [], 0
    fatal = []

    def work(d):
        if state.stop_reason:
            return None
        try:
            return process_doc(d, provider, price, cfg, state, out_dir)
        except Fatal as e:
            state.stop(str(e))
            fatal.append(str(e))
            return None

    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as ex:
        for r in ex.map(work, docs):
            if r is not None:
                results.append(r)
                done += 1
                if done % max(1, len(docs) // 5) == 0 or done == len(docs):
                    print(f"  {done}/{len(docs)}  spent ${state.spent:.4f}", flush=True)
    if fatal:
        raise Fatal(fatal[0])
    if state.stop_reason:
        print(f"  stopped early: {state.stop_reason}")
    s = summarize(results, docs)
    print(f"  F1 {s['f1']:.3f}  ok {s['ok']}  invalid {s['invalid']}  failed {s['failed']}  "
          f"cost ${s['cost']:.4f}  -> runs/{out_dir.name}")
    return s

# ---------------------------------------------------------------- scoring and report

def f1(tp: int, n_pred: int, n_gold: int) -> float:
    return 2 * tp / (n_pred + n_gold) if (n_pred + n_gold) else 1.0


def pct(xs: list, q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def summarize(recs: list, docs: list) -> dict:
    gold = {d["id"]: d["gold"] for d in docs}
    by_doc = {r["doc_id"]: r for r in recs}
    per = {f: [0, 0, 0] for f in FIELDS}
    doc_f1 = []
    for doc_id, g in gold.items():
        r = by_doc.get(doc_id)
        p = {tuple(x) for x in r["pred"]} if r else set()
        for f in FIELDS:
            gf = {x for x in g if x[0] == f}
            pf = {x for x in p if x[0] == f}
            per[f][0] += len(gf & pf)
            per[f][1] += len(pf)
            per[f][2] += len(gf)
        doc_f1.append(f1(len(g & p), len(p), len(g)))
    tp = sum(v[0] for v in per.values())
    npred = sum(v[1] for v in per.values())
    ngold = sum(v[2] for v in per.values())
    calls = [a["latency_s"] for r in recs for a in r.get("attempts", []) if a["status"] == 200]
    return {
        "docs": len(gold), "processed": len(recs),
        "ok": sum(r["status"] == "ok" for r in recs),
        "invalid": sum(r["status"] == "invalid" for r in recs),
        "failed": sum(r["status"] == "failed" for r in recs),
        "missing": len(gold) - len(recs),
        "invalid_first": sum(1 for r in recs if r.get("outputs") and r["outputs"][0]["problems"]),
        "repaired": sum(1 for r in recs for o in r.get("outputs", []) if o.get("repairs")),
        "retries": sum(max(len(r.get("attempts", [])) - len(r.get("outputs", [])), 0) for r in recs),
        "precision": tp / npred if npred else 0.0, "recall": tp / ngold if ngold else 0.0,
        "f1": f1(tp, npred, ngold), "mean_doc_f1": sum(doc_f1) / len(doc_f1) if doc_f1 else 0.0,
        "field_f1": {f: f1(*per[f]) for f in FIELDS},
        "p50": pct(calls, 0.5), "p95": pct(calls, 0.95),
        "tokens_in": sum(r.get("usage", {}).get("input", 0) for r in recs),
        "tokens_out": sum(r.get("usage", {}).get("output", 0) for r in recs),
        "cost": sum(r.get("cost_usd", 0.0) for r in recs),
    }


def secs(x: float) -> str:
    return "—" if x != x else f"{x:.1f}"


def load_run(run_dir: Path) -> list:
    return [json.loads(p.read_text()) for p in sorted((run_dir / "docs").glob("*.json"))]


def doc_counts(recs: list, docs: list) -> list:
    """Per document, in split order: (true positives, predicted labels, gold labels)."""
    by_doc = {r["doc_id"]: r for r in recs}
    out = []
    for d in docs:
        r = by_doc.get(d["id"])
        p = {tuple(x) for x in r["pred"]} if r else set()
        out.append((len(d["gold"] & p), len(p), len(d["gold"])))
    return out


def bootstrap_f1(counts: dict, n_docs: int, resamples: int = 10000, seed: int = 0) -> dict:
    """Micro F1 on documents resampled with replacement. Every run is scored on the same resamples,
    so differences between runs are paired. -> {run: [F1 per resample]}"""
    rng = random.Random(seed)
    out = {name: [] for name in counts}
    for _ in range(resamples):
        idx = [rng.randrange(n_docs) for _ in range(n_docs)]
        for name, c in counts.items():
            tp = npred = ngold = 0
            for i in idx:
                tp += c[i][0]
                npred += c[i][1]
                ngold += c[i][2]
            out[name].append(f1(tp, npred, ngold))
    return out


def interval(xs: list, level: float = 0.95) -> tuple:
    xs = sorted(xs)
    return xs[int((1 - level) / 2 * len(xs))], xs[int((1 + level) / 2 * len(xs)) - 1]


def field_values(labels, field: str) -> frozenset:
    return frozenset(v for k, v in labels if k == field)


def shared_cases(model_runs: dict, docs: list, min_share: float = 2 / 3):
    """(document, field) cases where most model runs give the same answer and the label says something
    else: a labelling convention, a label error or a definition the prompt reads differently, more often
    than one model's mistake. -> (cases, how many runs had to agree)"""
    need = max(2, math.ceil(min_share * len(model_runs) - 1e-9))
    preds = {name: {r["doc_id"]: {tuple(x) for x in r["pred"]} for r in recs} for name, recs in model_runs.items()}
    cases = []
    for d in docs:
        for f in FIELDS:
            g = field_values(d["gold"], f)
            answers = {name: field_values(p.get(d["id"], set()), f) for name, p in preds.items()}
            top, n = collections.Counter(answers.values()).most_common(1)[0]
            if top != g and n >= need:
                cases.append({"doc_id": d["id"], "field": f, "gold": sorted(g), "answer": sorted(top), "runs": n,
                              "errors": {name: len(g ^ a) for name, a in answers.items()}})
    return cases, need


def cmd_report(args) -> None:
    docs = load_split(args.split, args.text_col)
    gold = {d["id"]: d for d in docs}
    rows = []
    model_runs = {}
    err_dir = ROOT / "errors"
    for run_dir in sorted(RUNS.glob(f"*__{args.split}*")):
        meta = json.loads((run_dir / "meta.json").read_text())
        recs = load_run(run_dir)
        if not recs:
            continue
        s = summarize(recs, docs)
        rows.append((meta["model"], run_dir.name, s, recs))
        if meta["model"] != "rules":
            model_runs[run_dir.name] = recs
        lines = [f"# Mismatches: {run_dir.name}", "",
                 "Gold is the dataset label; pred is ours after normalisation. Upper-cased, as scored.", ""]
        for f in FIELDS:
            lines += [f"## {f}", "", "| doc | gold | pred | status |", "|---|---|---|---|"]
            for r in recs:
                g = sorted(v for k, v in gold[r["doc_id"]]["gold"] if k == f)
                p = sorted(v for k, v in (tuple(x) for x in r["pred"]) if k == f)
                if g != p:
                    lines.append(f"| {r['doc_id'][:8]} | {' ; '.join(g) or '—'} | {' ; '.join(p) or '—'} | {r['status']} |")
            lines.append("")
        err_dir.mkdir(exist_ok=True)
        (err_dir / f"{run_dir.name}.md").write_text("\n".join(lines))
    rows.sort(key=lambda x: -x[2]["f1"])
    resamples = getattr(args, "resamples", 10000)
    counts = {name: doc_counts(recs, docs) for _, name, _, recs in rows}
    boot = bootstrap_f1(counts, len(docs), resamples)
    out = [f"# Results on Kleister NDA `{args.split}`", "",
           f"Generated {utcnow().strftime('%Y-%m-%d %H:%M')} UTC by `python3 bench.py report`. "
           f"Prompt {PROMPT_VERSION}, text column `{args.text_col}`.", "",
           "| model | docs | F1 | F1 95% CI | P | R | party | date | jurisdiction | term | invalid 1st try "
           "| still invalid | failed | HTTP retries | p50 s | p95 s | cost | $ / 1000 docs |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for model, name, s, _ in rows:
        ff = s["field_f1"]
        per_k = s["cost"] / s["processed"] * 1000 if s["processed"] else 0
        lo, hi = interval(boot[name])
        out.append(f"| {model} | {s['processed']}/{s['docs']} | {s['f1']:.3f} | {lo:.3f}–{hi:.3f} | "
                   f"{s['precision']:.3f} | {s['recall']:.3f} | {ff['party']:.3f} | {ff['effective_date']:.3f} | "
                   f"{ff['jurisdiction']:.3f} | {ff['term']:.3f} | {s['invalid_first']} | {s['invalid']} | "
                   f"{s['failed']} | {s['retries']} | {secs(s['p50'])} | {secs(s['p95'])} | ${s['cost']:.3f} | "
                   f"${per_k:.2f} |")
    out += ["", "F1, P, R: micro-averaged over (document, field, value) labels, upper-cased, as in the dataset's "
            "own evaluation config; field columns are F1 per field. Partial runs are scored against the whole "
            "split (missing documents count as empty predictions). F1 95% CI: percentile bootstrap over "
            f"documents, {resamples} resamples, seed 0.", ""]
    token_rows = []
    for model, name, s, recs in rows:
        calls = sum(1 for r in recs for a in r.get("attempts", []) if a["status"] == 200)
        if calls:
            reasoning = sum(r.get("usage", {}).get("reasoning", 0) for r in recs)
            token_rows.append(f"| {model} | {calls} | {s['tokens_in']:,} | {s['tokens_in'] / calls:,.0f} | "
                              f"{s['tokens_out']:,} | {reasoning:,} |")
    if token_rows:
        out += ["## Tokens", "", "As reported by each API, summed over every answered request, retries included.", "",
                "| model | requests | input tokens | input per request | output tokens | of which reasoning |",
                "|---|---|---|---|---|---|"] + token_rows + [""]
    if len(rows) > 1:
        top_model, top_name, top_s, _ = rows[0]
        out += ["## Difference from the top model", "",
                f"F1 of `{top_model}` minus F1 of each model, on the same resampled documents (paired bootstrap).",
                "An interval that includes zero means this split cannot tell the two apart.", "",
                "| model | difference | 95% interval |", "|---|---|---|"]
        for model, name, s, _ in rows[1:]:
            lo, hi = interval([a - b for a, b in zip(boot[top_name], boot[name])])
            out.append(f"| {model} | {top_s['f1'] - s['f1']:+.3f} | {lo:+.3f} to {hi:+.3f} |")
        out.append("")
    if len(model_runs) >= 3:
        cases, need = shared_cases(model_runs, docs)
        shared = [f"# Shared disagreements: `{args.split}`", "",
                  f"(document, field) cases where at least {need} of the {len(model_runs)} model runs return the "
                  "same answer and the label differs. Upper-cased, as scored.", "",
                  "| doc | field | label | shared answer | runs |", "|---|---|---|---|---|"]
        for c in cases:
            shared.append(f"| {c['doc_id'][:8]} | {c['field']} | {' ; '.join(c['gold']) or '—'} | "
                          f"{' ; '.join(c['answer']) or '—'} | {c['runs']} |")
        err_dir.mkdir(exist_ok=True)
        (err_dir / f"shared-{args.split}.md").write_text("\n".join(shared) + "\n")
        out += ["## Errors shared across models", "",
                f"{len(cases)} (document, field) cases where at least {need} of the {len(model_runs)} model runs "
                f"give the same answer and the label differs; listed in `errors/shared-{args.split}.md`. "
                "Label errors: missed plus extra labels.", "",
                "| model | label errors | in shared cases |", "|---|---|---|"]
        for model, name, s, _ in rows:
            if name not in model_runs:
                continue
            total = sum(n_pred + n_gold - 2 * tp for tp, n_pred, n_gold in counts[name])
            in_shared = sum(c["errors"][name] for c in cases)
            out.append(f"| {model} | {total} | {in_shared} ({in_shared / total:.0%}) |" if total else
                       f"| {model} | 0 | 0 |")
        out.append("")
    (ROOT / ("results.md" if args.split == "dev-0" else f"results-{args.split}.md")).write_text("\n".join(out))
    print("\n".join(out))


def cmd_estimate(args) -> None:
    docs = load_split(args.split, args.text_col)
    total = 0.0
    for model, price in load_prices().items():
        est = estimate_usd(docs, price, price["provider"])
        total += est
        print(f"{model:18s} {price['provider']:10s} ~${est:.3f} for {len(docs)} docs")
    print(f"{'all models':18s} {'':10s} ~${total:.3f}")


def cmd_run(args) -> None:
    docs = load_split(args.split, args.text_col)
    try:
        run_model(args, docs)
    except Fatal as e:
        sys.exit(f"stopped: {e}")


def ask_keys() -> None:
    """Prompt for keys without echo, so they stay out of shell history. Enter skips a provider."""
    import getpass
    for prov in PROVIDERS.values():
        if not os.environ.get(prov.key_env):
            key = getpass.getpass(f"{prov.key_env} (Enter to skip): ").strip()
            if key:
                os.environ[prov.key_env] = key


def cmd_all(args) -> None:
    docs = load_split(args.split, args.text_col)
    prices = load_prices()
    if args.ask_keys:
        ask_keys()
    for model, price in prices.items():
        prov = PROVIDERS[price["provider"]]
        if not os.environ.get(prov.key_env):
            print(f"skip {model}: {prov.key_env} not set")
            continue
        args.model = model
        try:
            args.limit = 2
            run_model(args, docs)
            args.limit = 0
            run_model(args, docs)
        except Fatal as e:
            print(f"skip {model}: {e}")
    args.model = "rules"
    args.limit = 0
    run_model(args, docs)
    cmd_report(args)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--split", default="dev-0", choices=["dev-0", "train"])
        p.add_argument("--text-col", dest="text_col", default="text_best", choices=IN_COLUMNS[2:])

    def running(p):
        p.add_argument("--limit", type=int, default=0, help="first N documents only")
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--max-usd", dest="max_usd", type=float, default=3.0, help="per model; 0 = no cap")
        p.add_argument("--timeout", type=float, default=120.0, help="seconds per HTTP call")
        p.add_argument("--max-attempts", dest="max_attempts", type=int, default=5, help="HTTP attempts per call")
        p.add_argument("--output-tries", dest="output_tries", type=int, default=2,
                       help="how many times to ask again after an unusable answer, +1")
        p.add_argument("--max-output-tokens", dest="max_output_tokens", type=int, default=4000)
        p.add_argument("--reasoning-effort", dest="reasoning_effort", default="low",
                       help="OpenAI only; '' to leave the model default")
        p.add_argument("--max-failed-docs", dest="max_failed_docs", type=int, default=5)
        p.add_argument("--tag", default="")
        p.add_argument("--backoff-base", dest="backoff_base", type=float, default=2.0)
        p.add_argument("--backoff-cap", dest="backoff_cap", type=float, default=60.0)

    sub.add_parser("fetch").set_defaults(fn=cmd_fetch)
    p = sub.add_parser("estimate"); common(p); p.set_defaults(fn=cmd_estimate)
    p = sub.add_parser("run"); common(p); running(p)
    p.add_argument("--model", required=True, help="a model from prices.json, or 'rules'")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("all"); common(p); running(p); p.set_defaults(fn=cmd_all, model="")
    p.add_argument("--ask-keys", dest="ask_keys", action="store_true", help="prompt for API keys (no echo)")
    p = sub.add_parser("report"); common(p); p.set_defaults(fn=cmd_report)
    p.add_argument("--resamples", type=int, default=10000, help="bootstrap resamples for the intervals")
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
