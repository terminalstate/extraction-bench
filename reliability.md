# Structured output on a bad day

The [extraction benchmark](README.md) asked which model is accurate enough. This study asks what happens
around the answer: how often a first answer cannot be used, in which way, what gets it back, and at what
cost in money and time. It uses the same task (Kleister NDA, `dev-0`, 83 documents), the same prompt, schema,
scoring and prices; the code is [`reliability.py`](reliability.py).

This file was written on 2026-09-30, before any request of this study was sent. The six runs of the extraction
benchmark were already known; everything else below is a plan. Results will be added under it without
changing it.

## What changes

**How the answer is requested.** One inexpensive model per provider, every mechanism its API offers:

| model | prompt only | JSON mode | tool call | strict schema |
|---|---|---|---|---|
| gpt-6-luna | new | new | new, forced | from the extraction run (`json_schema`, strict) |
| claude-haiku-4-5 | new | — (no JSON mode) | from the extraction run (forced tool) | new (`output_config.format`) |
| deepseek-flash, thinking on | new | from the extraction run | new, optional ¹ | new, optional ¹ (beta endpoint) |
| deepseek-flash, thinking off | — | new | — | new, forced |

¹ DeepSeek rejects a forced tool choice in thinking mode (HTTP 400), so there the tool is offered and the user
message asks for it. If the strict endpoint rejects the schema's `null` types, the run switches to a variant
without `null` (`""` for a missing value, a list of zero or one terms) and says so.

The larger models' extraction runs (gpt-6-sol, claude-sonnet-5, deepseek-v4-pro) are included too, with
recovery requests for their unusable answers; `--extended` adds their prompt-only cells.

**How many requests at once.** Each of the three inexpensive models' extraction runs is repeated twice in a
row: 4 requests at a time (`repeat`), then all 83 at once (`burst`). The repeat is the same-hour baseline for
the burst, and the three runs of the same requests show how much answers move between runs.

**What happens after an unusable answer.** Five policies, all measured on the same failures:

| policy | extra requests | what it does |
|---|---|---|
| `local` | 0 | parses JSON out of prose or a code fence, removes trailing commas, closes cut-off JSON, reads near-miss keys (`partties`) and unit names (`year`), reformats unambiguous dates; a value it cannot read becomes empty and is reported as dropped, never guessed |
| `retry` | 1 | the same request again |
| `feedback` | 1 | the answer goes back with what was wrong with it (as a tool result for tool calls) |
| `bigger` | 1 | cut-off answers only: the same request with 4 times the output limit and time limits |
| `stack` | 0 or 1 | `local`; if the answer is still incomplete, one `feedback` request (`bigger` if it was cut off), repaired the same way |

Transport stays as in the extraction benchmark (backoff with jitter, `Retry-After`, a budget cap), with two
changes: a whole-call deadline (300 s) on top of the per-read timeout, and a request that timed out is sent once
more at most. The deadline exists because most HTTP clients' timeout limits each wait for data, not the call:
DeepSeek documents keeping a queued request open by sending empty lines, for up to 10 minutes. The tests show
it with a local server (`tests/test_reliability.py`).

## What is measured

- First answers, by what a plain `json.loads` plus the checks would see: `ok`, `wrapped` (valid JSON in prose
  or a fence), `invalid_json`, `schema` (breaks the schema the API was given), `value` (fits the schema, fails a
  value rule: a date string that is not a real YYYY-MM-DD date, a term of zero), `truncated`, `no_tool_call`,
  `empty`, `refusal`, `stopped` (e.g. DeepSeek's `insufficient_system_resource`), `http_failed`.
- Per policy: complete and partial answers recovered, F1 over the whole split, extra requests, dollars and
  seconds, and whether the recovered answers are as right as answers that were usable at once.
- Output tokens per answer and the share a lower output limit would have cut off.
- Under a burst: 429 and 5xx responses, `Retry-After` values, latency, time to the response headers, answers
  whose body began with whitespace (a request held in a queue), wall time.
- Run to run: documents with the same labels in all three runs of the same requests, and F1 of each run.
- For each cell: the share of answers slower than 5, 10, 30 and 60 s, and the share of spend on them.

## Expected, written before the run

1. With the prompt only, some first answers will fail a plain `json.loads`, mostly as `wrapped`. JSON mode
   removes `wrapped` and `invalid_json` but not `schema`; tool calls and strict schemas remove `schema` too.
2. Strict schemas will not remove `value` failures or truncation: this schema allows any string as a date, and
   no schema bounds reasoning.
3. Local repair will recover almost every `wrapped` and most `schema` failures at no cost, and no truncated
   answer from a reasoning model: when reasoning uses up the limit there is no answer to repair.
4. A plain retry will fix few `schema` and `value` failures, because the model tends to repeat itself (in the
   extraction run it fixed 0 of 2 bare years); feedback will fix most of them.
5. DeepSeek truncation comes from reasoning length. A 4 times larger limit will fix nearly all cut-off answers.
   With thinking off, truncation disappears, latency falls several-fold, and F1 stays within the noise.
6. With the tool optional, some DeepSeek answers will come as text instead of a tool call, even though the
   message asks for the tool.
7. All 83 requests at once will get no errors from DeepSeek (its documented limit is 2,500 concurrent
   requests); OpenAI and Anthropic may answer part of the burst with 429 and `Retry-After`, depending on the
   account's tier. Every request will finish within the retry budget, with a higher p95 than the same-hour
   repeat.
8. The same requests will give the same labels on most documents but not all, and F1 will move within its
   interval.

## Run it

```
python3 reliability.py plan                            # cells and an upper cost estimate (about $4)
python3 reliability.py run --ask-keys --limit 2 --no-burst   # two documents per cell, about $0.10
python3 reliability.py run --ask-keys                  # the rest; finished documents are not sent again
python3 reliability.py report                          # reliability-results.md, from local files only
```

`--max-usd` (default 6) caps the whole run; requests already in flight when it is reached still finish. A
provider whose requests fail on the way six times in a row is skipped for the rest of the run, and the same
command continues later. Requests and answers are kept in `runs-rel/`, which is not published.

## Changes after the two-document check (2026-09-30, 12:30 UTC)

The first check sent two documents to each OpenAI cell; the other providers' cells had not run yet.

- The API refused the OpenAI tool cell: "Function tools with reasoning_effort are not supported for gpt-6-luna
  in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'." The
  refused cell stays in the report as a result. Two cells are added with `reasoning_effort: none`: a forced tool
  call and, for comparison at the same setting, a strict schema.
- The cost column also shows each cell's tokens at the list price. OpenAI and DeepSeek cache a repeated prompt
  prefix on their own, so the second OpenAI cell paid less only because it ran right after the first.
- API keys can also come from a private file outside the project folder (`~/.config/extraction-bench/keys.env`,
  lines `NAME=value`), so a resumed run does not ask for them again.

Nothing in the expectations above was changed.

## Changes after the second two-document check (2026-09-30, 12:45 UTC)

This time every cell sent two documents.

- The APIs accepted every cell, including DeepSeek's strict mode with the schema's `null` types; the variant
  without `null` was not needed.
- claude-haiku-4-5 with the prompt only put both answers in a `json` code fence, one with notes after it. The
  feedback request sent `json.loads`' own error ("Expecting value"), which does not say what is wrong. Both
  answers came back fenced again, and one of them dropped the only party it had named before. The feedback now
  names the problem: "the JSON object came with a code fence or other text around it; send the JSON object
  alone". The two earlier feedback answers stay in the records as `feedback_v0`, are left out of the results,
  and both documents get the new request.
- The cost estimate now counts two recovery requests per unusable first answer, at the share seen so far in
  each cell: the prompt-only Haiku cell may need them for nearly every document.

## Results

Run on 2026-09-30, 15:50–16:09 UTC, after the two-document checks earlier that day, and finished on 2026-10-01
(see below). 23 cells: 17 new, 6 from the extraction runs; 1,625 new answers, $3.13. The numbers come from
`python3 reliability.py report` ([`reliability-results.md`](reliability-results.md)) and the records it reads.

**How the run went.** The harness made one mistake. Its stop for an unreachable provider counted rate-limited
requests as unreachable: after the OpenAI burst (section 4) it stopped OpenAI for the rest of the run and did not
keep the records of 38 of the 43 requests that had failed there. The five records it kept show 429 on every
attempt. The next day the same command sent those 38 documents again (38 at once, no errors) and made OpenAI's
recovery requests; the burst figures below count only the burst. Fixed since: a 429 or 5xx is an answer, not an
outage, and an answer is saved before a provider is stopped.

### 1. First answers: without enforcement, failures follow the model's habits

Usable first answers out of 83, as a plain `json.loads` plus the checks sees them:

| model | prompt only | JSON mode | tool call | strict schema |
|---|---|---|---|---|
| gpt-6-luna, reasoning low | 77 (6 `"year"`) | 78 (5 `"year"`) | refused by the API ¹ | 83 |
| gpt-6-luna, reasoning none | | | 83 | 83 |
| deepseek-flash, thinking | 78 (5 cut off) | 78 (3 cut off, 2 `"year"`) | 83 (optional tool) | 82 (1 cut off; optional tool) |
| deepseek-flash, no thinking | | 82 (1 `"year"`) | | 83 |
| claude-haiku-4-5 | 0 (83 in a code fence) | no JSON mode | 81 (2 dates) | 81 (2 dates) |

¹ "Function tools with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions. To use function
tools, use /v1/responses or set reasoning_effort to 'none'."

- With the prompt only, the result was the model's habit, not the instruction. Haiku put all 83 answers in a
  `json` code fence, two with notes after it; gpt-6-luna and deepseek-flash returned bare JSON every time.
- 15 of the 16 schema failures were the same slip: a one-year term written as `"unit": "year"` where the schema
  allows only `years`, on the same six documents for both providers. The 16th was a misspelled key (`partties`,
  deepseek-v4-pro). JSON mode does not check the schema, so it barely helped (6 → 5 for gpt-6-luna). No tool
  call had the slip, strict or not: a schema in the tool definition seems to steer the unit even when the API
  does not enforce it.
- Strict schemas removed the schema failures and nothing else. Haiku with `output_config.format` returned the
  same two bare years as with a forced tool call, and deepseek-flash in strict mode was cut off once. For Haiku
  the strict JSON output was also 13% cheaper than the forced tool call ($4.70 against $5.41 per 1000 documents
  at list price): 4,447 input and 51 output tokens per answer against 4,848 and 113.
- The four bare years (two documents, two Haiku cells each) come from templates with the day and month left
  blank: "made this day of , 2004". The labels have no date there.

### 2. What gets the answer back

All cells except the repeat and burst runs, pooled by what was wrong with the first answer. Complete answers /
documents tried (+ partial: usable, with the field that could not be read left empty):

| first answer | documents | local repair | same request | feedback | 4× output limit | stack |
|---|---|---|---|---|---|---|
| wrapped in a code fence | 83 | 82 (+1) | 0 | 0 | — | 82 (+1) |
| breaks the schema | 16 | 16 | 6 | 16 | — | 16 |
| fails a value rule (bare year) | 4 | 0 (+4) | 1 | 4 | — | 4 |
| cut off | 11 | 0 | 6 | — | 11 | 11 |

- **Shape is fixed locally, for nothing.** Taking the JSON out of the fence and reading `year` as `years` made 98
  of the 99 rejected answers complete without a request (the 99th had a bare year inside the fence). Asking again
  did not fix one fence: told to "send the JSON object alone", Haiku dropped the notes and kept the fence, 83
  times out of 83.
- **Wrong content needs feedback, not a retry.** For schema and value failures the same request again fixed 7 of
  20; the request that said what was wrong fixed 20 of 20. For the bare years, emptying the date locally also
  gives the labelled answer.
- **A cut-off reasoning model needs room.** All 11 cut-off answers were DeepSeek's and had no content at all:
  reasoning had used the whole 4,000-token limit, so there was nothing to repair. The same request again worked
  6 times out of 11, because reasoning length varies from run to run; 4 times the limit worked 11 times out of
  11, with 848 to 5,513 output tokens.
- **Recovered answers were as right as the others:** 0.82 label errors per answer repaired locally, 0.93 per
  answer usable as it came.
- **What it costs.** The stack (local repair first, one request only if the answer is still incomplete) made
  every cell usable on all 83 documents, one of them partial. It took no requests for gpt-6-luna; one for Haiku's
  83 fenced answers and two per cell for its bare years; 12–60 per 1000 documents for the thinking DeepSeek
  cells; and at most $0.14 per 1000 documents in any cell. After it, every mechanism gives the same F1 for the same model within the noise
  (largest gap: −0.008 for gpt-6-luna's prompt-only cell, interval −0.020 to +0.002), except DeepSeek's tool
  cells, below.

### 3. DeepSeek: thinking is where the time, the money and the cut-offs are

deepseek-flash, first answers:

| cell | reasoning tokens per answer | cut off | p50 / p95 s | $ / 1000 docs, list price | F1 after the stack |
|---|---|---|---|---|---|
| JSON mode, thinking (extraction run) | 1,188 | 3 | 4.1 / 14.9 | 2.63 | 0.889 |
| JSON mode, thinking (repeat, same hour as the rest) | 1,153 | 4 | 4.2 / 17.7 | 2.59 | — |
| prompt only, thinking | 1,230 | 5 | 4.4 / 17.9 | 2.67 | 0.891 |
| tool, thinking (optional) | 361 | 0 | 2.1 / 5.4 | 1.83 | 0.906 |
| strict tool, thinking (optional) | 368 | 1 | 2.1 / 5.3 | 1.84 | 0.906 |
| JSON mode, no thinking | 0 | 0 | 1.1 / 1.6 | 1.20 | 0.895 |
| strict tool, no thinking | 0 | 0 | 1.3 / 1.5 | 1.38 | 0.887 |

- Thinking off removed the cut-offs, brought p95 from 17.7 s to 1.6 s in the same hour and the list price to
  less than half, at the same F1 (0.895 against 0.889; difference interval −0.022 to +0.038).
- With a tool, thinking stayed on but shrank to a third: p50 2.1 s instead of 4.2 s, and F1 0.018 higher than
  JSON mode (intervals +0.001 to +0.043 and +0.006 to +0.032 for the two tool cells). These cells differ from JSON
  mode in two ways, the tool and the sentence asking for it, and this report makes eleven such comparisons:
  take the accuracy gain as a lead, not a result. The time and token savings are too large to need that caveat.
- The optional tool was always used: 166 of 166 first answers were tool calls.
- Reasoning length has a long tail. Of deepseek-flash's 511 answers with thinking on (every cell, retries
  included), 19 hit the 4,000 limit; among the rest the median is 543 output tokens and the 95th percentile 2,915.
  A limit of 2,000 would have cut off 16% of the answers, a limit of 1,000 a third.

### 4. All documents at once: three providers, three behaviours

| model | 4 at a time, same hour | all 83 at once |
|---|---|---|
| deepseek-flash | 131 s, p95 17.7 s | 21 s, p95 17.4 s; no 429, no sign of queueing |
| claude-haiku-4-5 | 36 s, p95 2.2 s | 9 s, p95 2.8 s; 59 responses were 429, all 83 answered within 3 attempts |
| gpt-6-luna | 46 s, p95 3.5 s | 40 answered at once; 43 failed after 5 attempts each |

- DeepSeek took the burst without a single 429 and without slowing down; it finished six times sooner.
- Anthropic answered 59 requests with 429, "Number of concurrent requests across all models has exceeded your
  organization's limit", and no `Retry-After` header. Exponential backoff with jitter (1.1–3.9 s) got every
  request through within three attempts.
- OpenAI ran into the account's tokens-per-minute limit (200,000, as its 429 message says). 40 requests were
  answered at once. The five failures whose records were kept got 429 on all five attempts over 22 seconds,
  although each 429 said to retry after 1–2 seconds. OpenAI counts a request against this limit by its output
  limit or its estimated size, whichever is larger (its rate-limit guide), so the 4,000-token output limit set for
  reasoning headroom also decides how many requests fit in a minute. `Retry-After` tells a client when to try
  once more, not when a burst will fit; pacing requests on the client from the rate-limit headers would.

### 5. The same requests, run again

The extraction run (morning), the repeat and the burst (16:01–16:06 UTC), on the documents with a usable first
answer in all three:

| model | documents | same labels in all three runs | F1 extraction run / repeat / burst | burst − extraction run |
|---|---|---|---|---|
| gpt-6-luna | 78 | 70 | 0.879 / 0.879 / 0.873 | −0.006 (−0.018 to +0.003) |
| deepseek-flash | 74 | 71 | 0.888 / 0.886 / 0.895 | +0.007 (+0.000 to +0.017) |
| claude-haiku-4-5 | 79 | 71 | 0.891 / 0.896 / 0.907 | +0.017 (+0.003 to +0.033) |

90–96% of these documents got the same labels every time; most changes were party names and dates. But Haiku's
F1 moved by 0.017 between two runs of the same requests, more than the interval over documents allows: the
interval from one run does not cover the next run. A gap of a point or two between two models or two prompts
needs more than one run before it means anything.

### 6. Deadlines

With thinking on, 17–22% of deepseek-flash's first answers took longer than 10 s and carried 32–50% of the
spend; for deepseek-v4-pro, 45% and 61%. With a tool, 1–2%. With thinking off, and in every OpenAI and Anthropic
cell, none. A 10-second deadline on a thinking DeepSeek model would throw away a sixth of the answers and a third
or more of the money.

### Against the expectations

1. Partly. Only Haiku wrapped its answers (83 of 83); gpt-6-luna and deepseek-flash never did. JSON mode did not
   remove the schema failures (6 → 5); tool calls and strict schemas did (0).
2. Yes: two bare years with Haiku's strict mode, one cut-off answer with DeepSeek's.
3. Yes: local repair completed 82 of 83 wrapped answers (+1 partial), 16 of 16 schema failures and none of the
   11 cut-off answers.
4. Yes for schema and value failures: the same request fixed 7 of 20, feedback 20 of 20. Not expected: feedback
   did not remove a single code fence (0 of 83).
5. Yes: 4 times the limit fixed 11 of 11; without thinking there were no cut-offs, p95 was 1.6 s instead of
   17.7 s, and F1 stayed within the noise.
6. No: with the tool optional and asked for, DeepSeek called it every time (166 of 166).
7. Partly: DeepSeek, no errors; Anthropic, 429 without `Retry-After` and every request finished; OpenAI, 43 of 83
   failed after their retries.
8. Partly: 90–96% of documents got the same labels in all three runs, but Haiku's F1 moved beyond its interval.

### What a team would take from this

- Repair the shape locally before asking again. It cost nothing and completed 98 of the 99 answers that
  `json.loads` and the schema check rejected; asking the model again did not remove a single code fence.
- When the content is wrong, say what is wrong. Feedback fixed 20 of 20; the same request again, 7.
- Give a reasoning model room, or turn reasoning off for extraction: here, without thinking, there were no
  cut-offs, the cost was less than half and F1 did not change.
- Pace bursts on the client, from the rate-limit headers. One provider took 83 requests at once, one slowed them
  down, one refused half of them for as long as the client kept trying. On OpenAI the output limit is part of the
  rate limit.
- Run an eval more than once before trusting a difference of a point or two.

### Limits

- One inexpensive model per provider, one dataset, one prompt. The larger models appear only with the mechanism
  of the extraction runs.
- Burst results depend on the account's tier and the hour: they show how each provider behaves, not its
  capacity.
- Latency compares within one run: the extraction runs were in the morning, everything else at 15:50–16:09 UTC.
- The OpenAI burst lost 38 failure records (above): the counts are certain, the attempts are inferred from the
  five that were kept.
- Local repair here unwraps fences and prose, closes cut-off JSON, reads near-miss keys and unit names, and never
  guesses a value. A repair that guessed would score differently.
