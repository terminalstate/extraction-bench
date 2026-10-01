# Structured output on a bad day: results

Generated 2026-10-01 09:19 UTC by `python3 reliability.py report`. Kleister NDA `dev-0`, prompt v1, output limit on the first request 4,000 tokens. A cell is one model asked one way; cells without `burst` sent 4 requests at a time.

## 1. First answers

What a pipeline that runs `json.loads` and the checks gets on the first request, per cell. `wrapped`: valid JSON inside prose or a code fence; `schema`: parses, but breaks the JSON schema the API was given; `value`: fits the schema, fails a value rule (a date that is not a real YYYY-MM-DD date, a term <= 0); `truncated`: cut off at the output limit; `no tool call`: the tool was optional and the model answered in text; `stopped`: the provider ended the answer (e.g. `insufficient_system_resource`). Usable and F1 are over all documents of the split: an unusable or missing answer counts as empty. Latency is comparable only within one run: bench.py cells ran on 2026-09-30 in the morning (UTC), the others later. Cost: what was paid, and in brackets the same tokens at the list price without cache and off-peak discounts; OpenAI and DeepSeek cache a repeated prompt prefix on their own, so what a cell paid depends on what ran just before it.

| cell | answered | ok | wrapped | invalid JSON | schema | value | truncated | no tool call | empty | refusal / stopped | HTTP failed | usable | F1 | p50 / p95 s | $ / 1000 docs paid (list) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-6-luna__prompt | 83/83 | 77 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 92.8% | 0.819 | 1.9 / 3.7 | $0.41 ($0.41) |
| gpt-6-luna__json_mode | 83/83 | 78 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 94.0% | 0.835 | 2.0 / 4.4 | $0.09 ($0.42) |
| gpt-6-luna__tool__nothink | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.880 | 1.7 / 2.8 | $0.42 ($0.42) |
| gpt-6-luna__strict | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.879 | 1.9 / 3.7 | $0.42 ($0.42) |
| gpt-6-luna__strict__nothink | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.874 | 1.7 / 2.6 | $0.41 ($0.41) |
| gpt-6-sol__strict | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.891 | 2.4 / 3.8 | $8.34 ($8.34) |
| deepseek-flash__prompt | 83/83 | 78 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 94.0% | 0.865 | 4.4 / 17.9 | $1.32 ($2.67) |
| deepseek-flash__json_mode | 83/83 | 78 | 0 | 0 | 2 | 0 | 3 | 0 | 0 | 0 | 0 | 94.0% | 0.849 | 4.1 / 14.9 | $2.56 ($2.63) |
| deepseek-flash__json_mode__nothink | 83/83 | 82 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 98.8% | 0.887 | 1.1 / 1.6 | $0.58 ($1.20) |
| deepseek-flash__tool | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.906 | 2.1 / 5.4 | $0.83 ($1.83) |
| deepseek-flash__strict | 83/83 | 82 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 98.8% | 0.898 | 2.1 / 5.3 | $0.84 ($1.84) |
| deepseek-flash__strict__nothink | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.887 | 1.3 / 1.5 | $0.62 ($1.38) |
| deepseek-v4-pro__json_mode | 83/83 | 79 | 0 | 0 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 95.2% | 0.860 | 8.3 / 35.0 | $8.52 ($8.98) |
| claude-haiku-4-5__prompt | 83/83 | 0 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0% | 0.000 | 1.5 / 2.9 | $4.44 ($4.44) |
| claude-haiku-4-5__tool | 83/83 | 81 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 97.6% | 0.885 | 1.7 / 3.1 | $5.41 ($5.41) |
| claude-haiku-4-5__strict | 83/83 | 81 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 97.6% | 0.893 | 1.6 / 2.5 | $4.70 ($4.70) |
| claude-sonnet-5__tool | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.892 | 2.9 / 4.4 | $14.88 ($14.88) |
| gpt-6-luna__strict__burst | 83/83 | 78 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 94.0% | 0.849 | 3.2 / 4.1 | $0.25 ($0.40) |
| gpt-6-luna__strict__repeat | 83/83 | 83 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 100.0% | 0.880 | 1.9 / 3.5 | $0.42 ($0.42) |
| deepseek-flash__json_mode__burst | 83/83 | 80 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 96.4% | 0.880 | 4.2 / 17.4 | $0.80 ($2.66) |
| deepseek-flash__json_mode__repeat | 83/83 | 79 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 95.2% | 0.867 | 4.2 / 17.7 | $0.76 ($2.59) |
| claude-haiku-4-5__tool__burst | 83/83 | 79 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 95.2% | 0.895 | 1.7 / 2.8 | $5.41 ($5.41) |
| claude-haiku-4-5__tool__repeat | 83/83 | 80 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 96.4% | 0.889 | 1.6 / 2.2 | $5.41 ($5.41) |
| gpt-6-luna__tool | — | rejected by the API: Function tools with reasoning_effort are not supported for gpt-6-luna in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'. | | | | | | | | | | | | | |

### What the unusable first answers got wrong

| cell | problem | answers |
|---|---|---|
| gpt-6-luna__prompt | term.unit invalid: … | 6 |
| gpt-6-luna__json_mode | term.unit invalid: … | 5 |
| deepseek-flash__prompt | cut off at the output limit | 5 |
| deepseek-flash__json_mode | cut off at the output limit | 3 |
| deepseek-flash__json_mode | term.unit invalid: … | 2 |
| deepseek-flash__json_mode__nothink | term.unit invalid: … | 1 |
| deepseek-flash__strict | cut off at the output limit | 1 |
| deepseek-v4-pro__json_mode | cut off at the output limit | 2 |
| deepseek-v4-pro__json_mode | extra partties | 1 |
| deepseek-v4-pro__json_mode | missing parties | 1 |
| deepseek-v4-pro__json_mode | parties is not a list of strings | 1 |
| deepseek-v4-pro__json_mode | term.unit invalid: … | 1 |
| claude-haiku-4-5__prompt | the JSON object came with a code fence or other text around it; send the JSON object alone | 83 |
| claude-haiku-4-5__tool | effective_date not YYYY-MM-DD: … | 2 |
| claude-haiku-4-5__strict | effective_date not YYYY-MM-DD: … | 2 |
| gpt-6-luna__strict__burst | http 429: Rate limit reached for gpt-6-luna in organization org-… on tokens per min (TPM): Limit 200000, Used  | 5 |
| deepseek-flash__json_mode__burst | cut off at the output limit | 3 |
| deepseek-flash__json_mode__repeat | cut off at the output limit | 4 |
| claude-haiku-4-5__tool__burst | effective_date not YYYY-MM-DD: … | 4 |
| claude-haiku-4-5__tool__repeat | effective_date not YYYY-MM-DD: … | 3 |

## 2. What gets an unusable answer back

All cells pooled except the repeat and burst runs, by what was wrong with the first answer. Each entry: complete answers / documents the policy was tried on (+ partial answers: usable, with a field emptied because its value could not be read). Policies:

- `local`: local repair only, no request.
- `retry`: the same request once more.
- `feedback`: one more request with the answer and what was wrong with it.
- `bigger`: one more request with a larger output limit (cut-off answers only).
- `stack`: local repair; if still incomplete, one feedback request (larger limit if cut off), repaired too.

| first answer | documents | local | retry | feedback | bigger | stack |
|---|---|---|---|---|---|---|
| wrapped | 83 | 82/83 (+1) | 0/83 | 0/83 | — | 82/83 (+1) |
| schema | 16 | 16/16 | 6/16 | 16/16 | — | 16/16 |
| value | 4 | 0/4 (+4) | 1/4 | 4/4 | — | 4/4 |
| truncated | 11 | 0/11 | 6/11 | — | 11/11 | 11/11 |

### Per cell: usable answers, F1 and the price of recovery

Usable: complete answers (+ partial). Extra: requests and dollars per 1000 documents on top of the first answers, and the 95th percentile of the added seconds over the documents that needed it.

| cell | unusable first | none | local | stack | stack not tried | F1 none | F1 local | F1 stack | extra requests / 1000 | extra $ / 1000 | added s p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-6-luna__prompt | 6 | 77 | 83 | 83 | — | 0.819 | 0.870 | 0.870 | 0 | $0.000 | — |
| gpt-6-luna__json_mode | 5 | 78 | 83 | 83 | — | 0.835 | 0.876 | 0.876 | 0 | $0.000 | — |
| deepseek-flash__prompt | 5 | 78 | 78 | 83 | — | 0.865 | 0.865 | 0.891 | 60 | $0.100 | 24.2 |
| deepseek-flash__json_mode | 5 | 78 | 80 | 83 | — | 0.849 | 0.868 | 0.889 | 36 | $0.063 | 17.3 |
| deepseek-flash__json_mode__nothink | 1 | 82 | 83 | 83 | — | 0.887 | 0.895 | 0.895 | 0 | $0.000 | — |
| deepseek-flash__strict | 1 | 82 | 82 | 83 | — | 0.898 | 0.898 | 0.906 | 12 | $0.016 | 9.8 |
| deepseek-v4-pro__json_mode | 4 | 79 | 81 | 83 | — | 0.860 | 0.878 | 0.893 | 24 | $0.118 | 33.6 |
| claude-haiku-4-5__prompt | 83 | 0 | 82 (+1) | 82 (+1) | — | 0.000 | 0.889 | 0.889 | 12 | $0.035 | 1.2 |
| claude-haiku-4-5__tool | 2 | 81 | 81 (+2) | 83 | — | 0.885 | 0.888 | 0.889 | 24 | $0.141 | 1.3 |
| claude-haiku-4-5__strict | 2 | 81 | 81 (+2) | 83 | — | 0.893 | 0.895 | 0.895 | 24 | $0.122 | 1.2 |

### Mechanisms after recovery

F1 over the whole split after the stack, each cell against the extraction run of the same model, on the same resampled documents (paired bootstrap, 95%).

| cell | against | F1 | F1 against | difference |
|---|---|---|---|---|
| gpt-6-luna__prompt | gpt-6-luna__strict | 0.870 | 0.879 | -0.008 (-0.020 to +0.002) |
| gpt-6-luna__json_mode | gpt-6-luna__strict | 0.876 | 0.879 | -0.003 (-0.013 to +0.006) |
| gpt-6-luna__tool__nothink | gpt-6-luna__strict | 0.880 | 0.879 | +0.001 (-0.009 to +0.011) |
| gpt-6-luna__strict__nothink | gpt-6-luna__strict | 0.874 | 0.879 | -0.005 (-0.016 to +0.005) |
| deepseek-flash__prompt | deepseek-flash__json_mode | 0.891 | 0.889 | +0.003 (-0.003 to +0.011) |
| deepseek-flash__json_mode__nothink | deepseek-flash__json_mode | 0.895 | 0.889 | +0.006 (-0.022 to +0.038) |
| deepseek-flash__tool | deepseek-flash__json_mode | 0.906 | 0.889 | +0.018 (+0.001 to +0.043) |
| deepseek-flash__strict | deepseek-flash__json_mode | 0.906 | 0.889 | +0.018 (+0.006 to +0.032) |
| deepseek-flash__strict__nothink | deepseek-flash__json_mode | 0.887 | 0.889 | -0.002 (-0.022 to +0.017) |
| claude-haiku-4-5__prompt | claude-haiku-4-5__tool | 0.889 | 0.889 | +0.000 (-0.011 to +0.012) |
| claude-haiku-4-5__strict | claude-haiku-4-5__tool | 0.895 | 0.889 | +0.007 (-0.007 to +0.021) |

### Are recovered answers right?

Label errors (missed plus extra labels) in the answers each policy recovered, next to the average over all first answers that were usable as they came.

| policy | answers recovered | label errors per answer |
|---|---|---|
| local | 103 | 0.82 |
| retry | 13 | 0.31 |
| feedback | 20 | 0.35 |
| bigger | 11 | 0.64 |
| stack | 114 | 0.79 |
| usable first answers | 1297 | 0.93 |

## 3. Output tokens and the output limit

First answers per cell, as each API reported them.

| cell | answers | input tokens, mean | output tokens, mean | output, median | reasoning, mean |
|---|---|---|---|---|---|
| gpt-6-luna__prompt | 83 | 3,735 | 82 | 51 | 37 |
| gpt-6-luna__json_mode | 83 | 3,735 | 94 | 53 | 46 |
| gpt-6-luna__tool__nothink | 83 | 3,896 | 52 | 50 | 0 |
| gpt-6-luna__strict | 83 | 3,831 | 75 | 49 | 26 |
| gpt-6-luna__strict__nothink | 83 | 3,831 | 47 | 45 | 0 |
| gpt-6-sol__strict | 83 | 3,831 | 67 | 63 | 18 |
| deepseek-flash__prompt | 83 | 3,809 | 1,275 | 759 | 1,230 |
| deepseek-flash__json_mode | 83 | 3,831 | 1,234 | 775 | 1,188 |
| deepseek-flash__json_mode__nothink | 83 | 3,806 | 47 | 45 | 0 |
| deepseek-flash__tool | 83 | 4,208 | 473 | 323 | 361 |
| deepseek-flash__strict | 83 | 4,208 | 480 | 315 | 368 |
| deepseek-flash__strict__nothink | 83 | 4,179 | 108 | 105 | 0 |
| deepseek-v4-pro__json_mode | 83 | 3,882 | 973 | 594 | 923 |
| claude-haiku-4-5__prompt | 83 | 4,066 | 75 | 67 | 0 |
| claude-haiku-4-5__tool | 83 | 4,848 | 113 | 112 | 0 |
| claude-haiku-4-5__strict | 83 | 4,447 | 51 | 48 | 0 |
| claude-sonnet-5__tool | 83 | 6,716 | 144 | 141 | 0 |
| gpt-6-luna__strict__burst | 78 | 3,854 | 80 | 49 | 31 |
| gpt-6-luna__strict__repeat | 83 | 3,831 | 84 | 49 | 34 |
| deepseek-flash__json_mode__burst | 83 | 3,831 | 1,260 | 777 | 1,214 |
| deepseek-flash__json_mode__repeat | 83 | 3,831 | 1,198 | 772 | 1,153 |
| claude-haiku-4-5__tool__burst | 83 | 4,848 | 113 | 112 | 0 |
| claude-haiku-4-5__tool__repeat | 83 | 4,848 | 113 | 112 | 0 |

Output tokens per answer (reasoning included), over answers that finished, and how many answers a lower limit would have cut off. Answers that hit the limit count as longer than any lower limit.

| model | mode | limit | answers | cut off | p50 | p95 | p99 | max | ≥ 1000 | ≥ 2000 | ≥ 3000 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-haiku-4-5 | — | 4000 | 591 | 0 | 81 | 125 | 208 | 271 | 0.0% | 0.0% | 0.0% |
| claude-sonnet-5 | — | 4000 | 83 | 0 | 141 | 169 | 189 | 301 | 0.0% | 0.0% | 0.0% |
| deepseek-flash | no thinking | 4000 | 168 | 0 | 68 | 119 | 130 | 196 | 0.0% | 0.0% | 0.0% |
| deepseek-flash | thinking | 4000 | 511 | 19 | 543 | 2915 | 3541 | 3824 | 32.3% | 15.7% | 8.0% |
| deepseek-flash | thinking | 16000 | 9 | 0 | 2460 | 5513 | 5513 | 5513 | 88.9% | 66.7% | 44.4% |
| deepseek-v4-pro | thinking | 4000 | 89 | 4 | 594 | 2566 | 2710 | 3514 | 33.7% | 15.7% | 5.6% |
| deepseek-v4-pro | thinking | 16000 | 2 | 0 | 1923 | 2816 | 2816 | 2816 | 100.0% | 50.0% | 0.0% |
| gpt-6-luna | reasoning low | 4000 | 432 | 0 | 49 | 197 | 294 | 459 | 0.0% | 0.0% | 0.0% |
| gpt-6-luna | reasoning none | 4000 | 166 | 0 | 48 | 65 | 74 | 130 | 0.0% | 0.0% | 0.0% |
| gpt-6-sol | reasoning low | 4000 | 83 | 0 | 63 | 122 | 158 | 201 | 0.0% | 0.0% | 0.0% |

## 4. All documents at once

Each bench.py run (4 requests at a time, 2026-09-30 morning UTC) was repeated twice in a row: 4 requests at a time (`repeat`), then every document at once (`burst`). Compare the burst with the repeat, which ran in the same hour. A burst cell counts only its first pass: `no record` is the number of documents of that pass without an answer on file. Latency is per successful request; `headers` is the time to the response headers; `keep-alive` counts answers whose body began with whitespace, the sign of a request held in a queue.

| cell | at once | requests | 429 | 5xx | timeouts / dropped | Retry-After seen | answered | failed | no record | p50 / p95 / max s | headers p95 s | keep-alive | wall s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gpt-6-luna__strict | 4 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 1.9 / 3.7 / 5.7 | — | — | 49.4 |
| gpt-6-luna__strict__repeat | 4 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 1.9 / 3.5 / 9.5 | 3.5 | 0 | 45.7 |
| gpt-6-luna__strict__burst | 83 | 65 | 25 | 0 | 0 | 1, 2 | 40 | 5 | 38 | 1.7 / 3.1 / 4.3 | 3.1 | 0 | 31.5 |
| deepseek-flash__json_mode | 4 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 4.1 / 14.9 / 19.0 | — | — | 149.3 |
| deepseek-flash__json_mode__repeat | 4 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 4.2 / 17.7 / 20.5 | 0.7 | 0 | 131.3 |
| deepseek-flash__json_mode__burst | 83 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 4.2 / 17.4 / 20.2 | 0.5 | 0 | 20.5 |
| claude-haiku-4-5__tool | 4 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 1.7 / 3.1 / 7.5 | — | — | 42.6 |
| claude-haiku-4-5__tool__repeat | 4 | 83 | 0 | 0 | 0 | — | 83 | 0 | 0 | 1.6 / 2.2 / 2.6 | 2.2 | 0 | 36.2 |
| claude-haiku-4-5__tool__burst | 83 | 142 | 59 | 0 | 0 | — | 83 | 0 | 0 | 1.7 / 2.8 / 3.2 | 2.8 | 0 | 9.4 |

### The same requests, run again

On the documents with a usable first answer in every run: how many came out with the same labels every time, F1 of each run on those documents, and each repeat minus the bench.py run with its paired 95% interval. The interval resamples documents; it does not know that the model may answer the same document differently next time.

| model | documents | same labels in every run | F1 bench / repeat / burst | repeat − bench | burst − bench |
|---|---|---|---|---|---|
| gpt-6-luna | 78 | 70 | 0.879 / 0.879 / 0.873 | +0.000 (-0.009 to +0.008) | -0.006 (-0.018 to +0.003) |
| deepseek-flash | 74 | 71 | 0.888 / 0.886 / 0.895 | -0.002 (-0.006 to +0.000) | +0.007 (+0.000 to +0.017) |
| claude-haiku-4-5 | 79 | 71 | 0.891 / 0.896 / 0.907 | +0.006 (-0.003 to +0.017) | +0.017 (+0.003 to +0.033) |

## 5. What a deadline would cut

Share of first answers that took longer than a deadline, and their share of the spend. A client that gives up at the deadline gets no answer; whether the provider still bills the request is not documented by these three APIs, so the last column is the spend at stake. bench.py cells ran on 2026-09-30 in the morning (UTC), the other cells later: compare latency within one run.

| cell | answers | > 5 s | > 10 s | > 30 s | > 60 s | spend on answers > 10 s |
|---|---|---|---|---|---|---|
| gpt-6-luna__prompt | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-luna__json_mode | 83 | 2.4% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-luna__tool__nothink | 83 | 3.6% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-luna__strict | 83 | 1.2% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-luna__strict__nothink | 83 | 1.2% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-sol__strict | 83 | 2.4% | 0.0% | 0.0% | 0.0% | 0.0% |
| deepseek-flash__prompt | 83 | 45.8% | 21.7% | 0.0% | 0.0% | 39.7% |
| deepseek-flash__json_mode | 83 | 45.8% | 16.9% | 0.0% | 0.0% | 31.6% |
| deepseek-flash__json_mode__nothink | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| deepseek-flash__tool | 83 | 8.4% | 1.2% | 0.0% | 0.0% | 5.1% |
| deepseek-flash__strict | 83 | 6.0% | 2.4% | 0.0% | 0.0% | 8.3% |
| deepseek-flash__strict__nothink | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| deepseek-v4-pro__json_mode | 83 | 80.7% | 44.6% | 8.4% | 0.0% | 61.4% |
| claude-haiku-4-5__prompt | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| claude-haiku-4-5__tool | 83 | 1.2% | 0.0% | 0.0% | 0.0% | 0.0% |
| claude-haiku-4-5__strict | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| claude-sonnet-5__tool | 83 | 2.4% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-luna__strict__burst | 78 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| gpt-6-luna__strict__repeat | 83 | 1.2% | 0.0% | 0.0% | 0.0% | 0.0% |
| deepseek-flash__json_mode__burst | 83 | 44.6% | 20.5% | 0.0% | 0.0% | 49.8% |
| deepseek-flash__json_mode__repeat | 83 | 43.4% | 19.3% | 0.0% | 0.0% | 47.4% |
| claude-haiku-4-5__tool__burst | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| claude-haiku-4-5__tool__repeat | 83 | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |

New requests in runs-rel/ cost $3.13 in total (bench.py runs not included).
