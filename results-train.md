# Results on Kleister NDA `train`

Generated 2026-09-30 08:39 UTC by `python3 bench.py report`. Prompt v1, text column `text_best`.

| model | docs | F1 | F1 95% CI | P | R | party | date | jurisdiction | term | invalid 1st try | still invalid | failed | HTTP retries | p50 s | p95 s | cost | $ / 1000 docs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | 254/254 | 0.502 | 0.476–0.529 | 0.545 | 0.465 | 0.271 | 0.560 | 0.965 | 0.243 | 0 | 0 | 0 | 0 | — | — | $0.000 | $0.00 |

F1, P, R: micro-averaged over (document, field, value) labels, upper-cased, as in the dataset's own evaluation config; field columns are F1 per field. Partial runs are scored against the whole split (missing documents count as empty predictions). F1 95% CI: percentile bootstrap over documents, 10000 resamples, seed 0.
