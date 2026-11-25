# Evaluation results

Generated 2026-11-25 13:32 UTC by `eval/run_eval.py` on `eval/golden.jsonl` (33 questions; 30 answerable).

## Retrieval (deterministic, no LLM)

| Mode | hit@6 | MRR@6 | LOTO | Aviation | Data center | Work orders |
|---|---|---|---|---|---|---|
| hybrid | 0.933 | 0.907 | 0.90 | 0.88 | 1.00 | 1.00 |
| vector | 0.933 | 0.816 | 0.90 | 0.88 | 1.00 | 1.00 |
| bm25 | 0.967 | 0.868 | 0.90 | 1.00 | 1.00 | 1.00 |

## Answers (model `claude-haiku-5-5`)

| Metric | Value |
|---|---|
| Keyword coverage (answerable) | 0.933 |
| Citation validity (every [n] maps to a retrieved chunk) | 1.000 |
| Answerable questions answered with citations | 0.967 |
| Correct refusals (unanswerable) | 3/3 |
| False refusals (answerable) | 1/30 |
| Latency p50 / p95 (end to end) | 1.70 s / 3.42 s |
| Avg tokens in / out | 3429 / 345 |
| Cost: total / per query | $0.0170 / $0.00052 |

<details><summary>Per-question answer results</summary>

| id | category | refused | cites | kw coverage | missing | ms |
|---|---|---|---|---|---|---|
| q01 | loto | False | 2 | 1.00 |  | 1695 |
| q02 | loto | False | 4 | 0.50 | key | 1962 |
| q03 | loto | False | 2 | 0.50 | capable of being locked out | 4569 |
| q04 | loto | False | 2 | 1.00 |  | 2256 |
| q05 | loto | False | 3 | 1.00 |  | 1432 |
| q06 | loto | False | 3 | 1.00 |  | 3106 |
| q07 | loto | False | 2 | 1.00 |  | 3186 |
| q08 | loto | False | 2 | 1.00 |  | 1589 |
| q09 | loto | False | 1 | 1.00 |  | 3418 |
| q10 | loto | False | 2 | 1.00 |  | 1842 |
| q11 | aviation | False | 2 | 1.00 |  | 1664 |
| q12 | aviation | True | 2 | 0.00 | Transport Canada, twelve | 1194 |
| q13 | aviation | False | 1 | 1.00 |  | 825 |
| q14 | aviation | False | 1 | 1.00 |  | 1643 |
| q15 | aviation | False | 1 | 1.00 |  | 921 |
| q16 | aviation | False | 1 | 1.00 |  | 1126 |
| q17 | aviation | False | 2 | 1.00 |  | 2088 |
| q18 | aviation | False | 2 | 1.00 |  | 3716 |
| q19 | datacenter | False | 2 | 1.00 |  | 2954 |
| q20 | datacenter | False | 3 | 1.00 |  | 2300 |
| q21 | datacenter | False | 3 | 1.00 |  | 1064 |
| q22 | datacenter | False | 2 | 1.00 |  | 1714 |
| q23 | datacenter | False | 4 | 1.00 |  | 1768 |
| q24 | datacenter | False | 2 | 1.00 |  | 1696 |
| q25 | datacenter | False | 2 | 1.00 |  | 1439 |
| q26 | workorders | False | 6 | 1.00 |  | 1227 |
| q27 | workorders | False | 2 | 1.00 |  | 2226 |
| q28 | workorders | False | 5 | 1.00 |  | 1957 |
| q29 | workorders | False | 6 | 1.00 |  | 1696 |
| q30 | workorders | False | 1 | 1.00 |  | 880 |
| q31 | unanswerable | True | 3 | - |  | 1125 |
| q32 | unanswerable | True | 0 | - |  | 992 |
| q33 | unanswerable | True | 0 | - |  | 1365 |

</details>

Hybrid retrieval misses: q02, q12
