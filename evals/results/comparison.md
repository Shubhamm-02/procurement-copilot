# Architecture comparison — public evaluation set

- **LLM provider used for this run:** `deterministic (no LLM key)`
- **Cases:** 6 (identical set for both architectures)

| Metric | Single agent (A) | Staged / 2-agent (B) |
|---|---:|---:|
| Cases passing minimum checks | 6/6 | 6/6 |
| Avg latency (ms) | 4.5 | 1.7 |
| Avg LLM calls / case | 0 | 0 |
| Avg tool calls / case | 7 | 7 |
| Cases with failures | 0 | 0 |

## Per-case detail

| Case | Title | A pass | A ms | A LLM | B pass | B ms | B LLM |
|---|---|:--:|--:|--:|:--:|--:|--:|
| PUB-01 | Low-value approved vendor | ✓ | 16.9 | 0 | ✓ | 1.8 | 0 |
| PUB-02 | Existing alternatives + new vendor | ✓ | 2.7 | 0 | ✓ | 1.7 | 0 |
| PUB-03 | Sensitive source-code access | ✓ | 1.8 | 0 | ✓ | 1.8 | 0 |
| PUB-04 | Budget shortfall + new sensitive vendor | ✓ | 1.8 | 0 | ✓ | 1.6 | 0 |
| PUB-05 | Incomplete request + prompt injection | ✓ | 1.8 | 0 | ✓ | 1.6 | 0 |
| PUB-06 | Vendor-risk API unavailable | ✓ | 2.0 | 0 | ✓ | 1.5 | 0 |
