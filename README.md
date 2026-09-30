# Contract field extraction with LLMs: accuracy, failures and cost

The same extraction task, the same prompt, several model APIs, scored against human labels on a public
benchmark. The question is the one a team asks before shipping: which model is accurate enough, how often
does it return something unusable, and what does a thousand documents cost.

## Task

[Kleister NDA](https://github.com/applicaai/kleister-nda): non-disclosure agreements filed with the SEC
(EDGAR), as OCR text of the PDFs, with labels for four fields:

| field | example label | note |
|---|---|---|
| party | `Nike_Inc.`, `Eric_Dean_Sprunk` | usually two per document |
| effective_date | `2001-04-18` | often absent |
| jurisdiction | `Oregon` | governing-law clause |
| term | `2_years`, `18_months` | often absent; the document's own unit |

Evaluation uses the `dev-0` split (83 documents, 334 labels). The prompt and the normalisation rules were
written from the dataset README and the `train` split only; `dev-0` is not used for tuning.

## Results

`dev-0`: 83 documents, 334 labels, prompt v1, one run per model on 2026-09-30.

| model | F1 | 95% CI | party | date | jurisdiction | term | unusable answers ¹ | p50 / p95 s | $ / 1000 docs |
|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5 | 0.892 | 0.846–0.930 | 0.846 | 0.919 | 0.987 | 0.848 | 0 | 2.9 / 4.4 | $14.88 |
| gpt-6-sol | 0.891 | 0.845–0.928 | 0.838 | 0.951 | 0.994 | 0.806 | 0 | 2.4 / 3.8 | $8.34 |
| claude-haiku-4-5 | 0.885 | 0.840–0.924 | 0.836 | 0.921 | 0.974 | 0.861 | 2 → 2 | 1.7 / 3.1 | $5.55 |
| gpt-6-luna | 0.879 | 0.830–0.921 | 0.827 | 0.927 | 0.981 | 0.818 | 0 | 1.9 / 3.7 | $0.42 |
| deepseek-flash | 0.879 | 0.831–0.920 | 0.824 | 0.928 | 0.987 | 0.806 | 5 → 1 | 4.4 / 14.9 | $2.70 |
| deepseek-v4-pro | 0.878 | 0.828–0.920 | 0.829 | 0.927 | 0.980 | 0.787 | 4 → 2 | 8.4 / 46.6 | $9.01 |
| regex baseline | 0.523 | 0.475–0.570 | 0.350 | 0.593 | 0.907 | 0.244 | — | — | $0 |

¹ Answers that failed validation on the first request → documents still without a usable answer after one more
request, scored as empty.

Settings: OpenAI models with `reasoning_effort: low`; DeepSeek models as served by default, which means they
reason before answering; Anthropic models without extended thinking; output limit 4,000 tokens. Precision, recall,
token counts and the paired comparisons are in [`results.md`](results.md), every mismatch in [`errors/`](errors/).

### Accuracy is a tie, price is not

All six models land between 0.878 and 0.892. Scored on the same resampled documents, the gap between the top
model and each of the others has a 95% interval that includes zero; the widest is +0.014 (−0.010 to +0.044).
Every model is 0.35–0.37 above the regex baseline, far outside the noise.

The price does differ: $0.42 per thousand documents for gpt-6-luna, $14.88 for claude-sonnet-5, a factor of 35.
Price per token is not price per document either. The same request came to 3.8–3.9k input tokens on the OpenAI
and DeepSeek models, 4.9k on claude-haiku-4-5 and 6.7k on claude-sonnet-5, and about 95% of the DeepSeek models'
output tokens were reasoning.

### Most errors are shared, and most shared errors are about the labels

Each model makes 74–83 label errors (a label missed or a label added). 78–91% of them fall into 36
(document, field) cases where at least four of the six models give the same answer and the label says something
else (`errors/shared-dev-0.md`). All 36 were read against the documents; the quotes are in
[`error-review.md`](error-review.md).

| what the case is | cases | example |
|---|---|---|
| the name is written differently | 12 | the document says "II-VI Incorporated", the label `II-VI_INC.`; "L.L.C." against `LLC` |
| the label contradicts the text or misses a value | 5 | "This Agreement shall continue in full force and effect for a period of two years", and no `term` label |
| the prompt and the labels define the field differently | 6 | when an NDA states no duration of its own, the labels take how long confidentiality lasts as the term; the prompt rules that out |
| who counts as a party | 4 | a defined group ("the Affiliated Companies") or its members |
| the dataset's input does not ask for the field | 3 | a party is labelled, but the input asks for the jurisdiction only |
| reasonable readings differ | 6 | an amendment in the same filing moves the end of the NDA from the second anniversary to the fourth |

The other 7–17 errors per model are where a model departs from most of the others: a role instead of a name
("Recipient", "Employee"), a template placeholder taken as a party, a date a month off, and the documents lost to
unusable answers.

### Structured output still has to be checked

11 answers failed the local check on the first request:

- claude-haiku-4-5, answering through a forced tool call, returned a bare year (`"2004"`) as the effective date
  on two documents. The tool schema allowed any string; the local check did not. Asked again, it gave the same
  answer both times, and both documents scored as empty.
- DeepSeek in JSON mode, which has no schema, returned the unit `"year"` where only `"years"` is allowed, and
  once spelled the key `partties`.
- 7 DeepSeek answers were cut off because reasoning used up the output limit. deepseek-v4-pro hit it twice on
  each of two documents, and those two documents account for 10 of its 14 errors that the other models do not
  share.

The second request fixed 6 of the 11; 5 documents scored as empty. Repeating the same request does not help when
the model gives the same answer every time.

No HTTP errors or retries happened in these runs, so the retry paths were exercised only by the fault-injection
tests. The six runs cost $3.39 in total; the DeepSeek runs fell in DeepSeek's peak hours and are priced at the
full rate.

### What a team would take from this

- Once accuracy is tied, choose on cost, latency and how the model fails. Here the cheapest model costs a
  thirty-fifth of the most expensive one and is inside its noise.
- Agree on the field definitions and build the eval set from them before comparing models. At this level the
  definitions and the labels move the score more than the choice of model does.
- Validate every answer locally, whatever the API promises; keep the fields that pass instead of discarding a
  partly valid answer; give reasoning models room in the output limit, and send slow ones to a queue rather than
  a user's request (p95 of 15 and 47 s for the two DeepSeek models, under 5 s for the rest).

## How it works

- **One prompt for every model.** Only the output mechanism differs: strict JSON schema (OpenAI),
  a forced tool call (Anthropic), JSON mode without a schema (DeepSeek).
- **Nothing the model returns is trusted.** Every answer is parsed and validated locally: shape, types,
  real calendar dates, allowed units. Empty, truncated, refused or malformed answers are counted, and the
  model is asked once more; if the second answer is also unusable the document scores as empty.
- **Transport failures are handled and counted.** Per-call timeout; retries with exponential backoff and
  jitter on 408/409/429/5xx, timeouts and dropped connections, honouring `Retry-After`; 400 fails one
  document; 401/403/404 stop the run at once.
- **Spend is capped.** An estimate is checked against `--max-usd` before the first call, and the run stops
  when actual spend reaches it. Cost is computed from the token usage each API reports and `prices.json`
  (including DeepSeek's off-peak discount by request time).
- **Runs resume.** Each document's full record (attempts, raw answers, usage, cost) is written to
  `runs/<model>__<prompt>__<split>/docs/`; a rerun skips finished documents and pays only for the rest.
- **Scoring** follows the dataset's own evaluation config: micro-averaged F1 over upper-cased
  `field=value` labels, plus F1 per field. Model answers are normalised the way the labels are: commas
  dropped, `&` as `and`, spaces as underscores, "State of" removed from jurisdictions, terms as
  `{number}_{unit}`. This is a reimplementation of the metric, not the official GEval binary.
- **Uncertainty is part of the report.** `report` adds a bootstrap 95% interval for F1, the paired difference
  between the top model and each of the others, the token counts each API reported, and the (document, field)
  cases where most models agree with each other and not with the label (`errors/shared-<split>.md`).
- **Standard library only.** Python 3.9+, no packages to install.

## Reproduce

```
python3 bench.py fetch          # dataset files at a pinned commit, sha256-checked
python3 bench.py estimate       # expected cost per model for dev-0
python3 bench.py run --model rules

python3 bench.py all --ask-keys # asks for the keys without echo (Enter skips a provider);
                                # each model with a key: 2-document smoke test, then all 83; then report
```

One model at a time: `python3 bench.py run --model claude-haiku-4-5 --limit 5`. Models and prices live in
`prices.json`; add a model there to include it.

Tests run against a local fake of the three APIs with scripted faults (429 with `Retry-After`, 500,
timeout, dropped connection, 400, 401, invalid JSON, empty answer, code fences, schema violations,
truncation, budget stop, resume): `python3 -m unittest discover -s tests`.

## Known limits

- 83 documents: the 95% intervals are about ±0.04 F1, so differences of a couple of points between models cannot
  be separated on this split.
- One run per model with default sampling; a repeat run can move the numbers slightly.
- `dev-0` is used only for evaluation. Prompt v1 was written from the dataset README and `train` and was not
  changed after these results. The review points at the definition of `term`; a prompt changed because of it
  would have to be judged on documents other than `dev-0`.
- The input lists the fields to extract for each document. In 3 `dev-0` documents it lists only `jurisdiction`
  while the labels also contain a party; the benchmark follows the input, so no model can score those 3 labels.
- The labels follow conventions the text does not always make obvious. In one `train` document the
  agreement "is made this 6th of February, 1999", the signatures are dated 2/9/99 and 02/08/99, and the
  label is `1999-02-08`. The mismatch files make such cases easy to find.
- The grouping in `error-review.md` is one reader's judgement.
- Only the text layer is used (`text_best` column); layout and images are not.
- Prices change; `prices.json` records the date they were checked. DeepSeek's peak hours do not apply on Chinese
  public holidays, which the cost calculation does not know about.

## Data

Kleister NDA by Applica.ai, documents from SEC EDGAR. The data is downloaded from the original repository
at run time and is not redistributed here.

---

Built by [terminalstate](https://terminalstate.dev) — payment and AI integration reliability. Questions: hello@terminalstate.dev
