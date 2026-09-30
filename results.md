# Results on Kleister NDA `dev-0`

Generated 2026-09-30 08:41 UTC by `python3 bench.py report`. Prompt v1, text column `text_best`.

| model | docs | F1 | F1 95% CI | P | R | party | date | jurisdiction | term | invalid 1st try | still invalid | failed | HTTP retries | p50 s | p95 s | cost | $ / 1000 docs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-sonnet-5 | 83/83 | 0.892 | 0.846–0.930 | 0.871 | 0.913 | 0.846 | 0.919 | 0.987 | 0.848 | 0 | 0 | 0 | 0 | 2.9 | 4.4 | $1.235 | $14.88 |
| gpt-6-sol | 83/83 | 0.891 | 0.845–0.928 | 0.878 | 0.904 | 0.838 | 0.951 | 0.994 | 0.806 | 0 | 0 | 0 | 0 | 2.4 | 3.8 | $0.692 | $8.34 |
| claude-haiku-4-5 | 83/83 | 0.885 | 0.840–0.924 | 0.859 | 0.913 | 0.836 | 0.921 | 0.974 | 0.861 | 2 | 2 | 0 | 0 | 1.7 | 3.1 | $0.461 | $5.55 |
| gpt-6-luna | 83/83 | 0.879 | 0.830–0.921 | 0.858 | 0.901 | 0.827 | 0.927 | 0.981 | 0.818 | 0 | 0 | 0 | 0 | 1.9 | 3.7 | $0.035 | $0.42 |
| deepseek-flash | 83/83 | 0.879 | 0.831–0.920 | 0.868 | 0.889 | 0.824 | 0.928 | 0.987 | 0.806 | 5 | 1 | 0 | 0 | 4.4 | 14.9 | $0.224 | $2.70 |
| deepseek-v4-pro | 83/83 | 0.878 | 0.828–0.920 | 0.875 | 0.880 | 0.829 | 0.927 | 0.980 | 0.787 | 4 | 2 | 0 | 0 | 8.4 | 46.6 | $0.748 | $9.01 |
| rules | 83/83 | 0.523 | 0.475–0.570 | 0.580 | 0.476 | 0.350 | 0.593 | 0.907 | 0.244 | 0 | 0 | 0 | 0 | — | — | $0.000 | $0.00 |

F1, P, R: micro-averaged over (document, field, value) labels, upper-cased, as in the dataset's own evaluation config; field columns are F1 per field. Partial runs are scored against the whole split (missing documents count as empty predictions). F1 95% CI: percentile bootstrap over documents, 10000 resamples, seed 0.

## Tokens

As reported by each API, summed over every answered request, retries included.

| model | requests | input tokens | input per request | output tokens | of which reasoning |
|---|---|---|---|---|---|
| claude-sonnet-5 | 83 | 557,466 | 6,716 | 11,990 | 0 |
| gpt-6-sol | 83 | 317,934 | 3,831 | 5,601 | 1,506 |
| claude-haiku-4-5 | 85 | 412,725 | 4,856 | 9,562 | 0 |
| gpt-6-luna | 83 | 317,934 | 3,831 | 6,187 | 2,162 |
| deepseek-flash | 88 | 339,411 | 3,857 | 112,268 | 108,214 |
| deepseek-v4-pro | 87 | 343,270 | 3,946 | 90,722 | 86,384 |

## Difference from the top model

F1 of `claude-sonnet-5` minus F1 of each model, on the same resampled documents (paired bootstrap).
An interval that includes zero means this split cannot tell the two apart.

| model | difference | 95% interval |
|---|---|---|
| gpt-6-sol | +0.001 | -0.018 to +0.019 |
| claude-haiku-4-5 | +0.006 | -0.013 to +0.028 |
| gpt-6-luna | +0.013 | -0.009 to +0.041 |
| deepseek-flash | +0.013 | -0.010 to +0.041 |
| deepseek-v4-pro | +0.014 | -0.010 to +0.044 |
| rules | +0.369 | +0.309 to +0.425 |

## Errors shared across models

36 (document, field) cases where at least 4 of the 6 model runs give the same answer and the label differs; listed in `errors/shared-dev-0.md`. Label errors: missed plus extra labels.

| model | label errors | in shared cases |
|---|---|---|
| claude-sonnet-5 | 74 | 63 (85%) |
| gpt-6-sol | 74 | 67 (91%) |
| claude-haiku-4-5 | 79 | 62 (78%) |
| gpt-6-luna | 83 | 71 (86%) |
| deepseek-flash | 82 | 69 (84%) |
| deepseek-v4-pro | 82 | 68 (83%) |
