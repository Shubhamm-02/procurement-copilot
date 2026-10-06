# Architecture comparison — public evaluation set

- **LLM provider used for this run:** `groq:openai/gpt-oss-120b`
- **Cases:** 6 (identical set for both architectures)

| Metric | Single agent (A) | Staged / 2-agent (B) |
|---|---:|---:|
| Cases passing minimum checks | 6/6 | 6/6 |
| Avg latency (ms) | 1217.7 | 13985.4 |
| Avg LLM calls / case | 1 | 2 |
| Avg tool calls / case | 7 | 7 |
| Cases with failures | 0 | 0 |

## Per-case detail

| Case | Title | A pass | A ms | A LLM | B pass | B ms | B LLM |
|---|---|:--:|--:|--:|:--:|--:|--:|
| PUB-01 | Low-value approved vendor | ✓ | 722.5 | 1 | ✓ | 2265.3 | 2 |
| PUB-02 | Existing alternatives + new vendor | ✓ | 1137.2 | 1 | ✓ | 14239.8 | 2 |
| PUB-03 | Sensitive source-code access | ✓ | 1023.8 | 1 | ✓ | 14741.2 | 2 |
| PUB-04 | Budget shortfall + new sensitive vendor | ✓ | 2170.7 | 1 | ✓ | 16343.8 | 2 |
| PUB-05 | Incomplete request + prompt injection | ✓ | 1443.6 | 1 | ✓ | 20696.2 | 2 |
| PUB-06 | Vendor-risk API unavailable | ✓ | 808.4 | 1 | ✓ | 15626.2 | 2 |
