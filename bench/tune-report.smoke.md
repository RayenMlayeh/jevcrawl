# jevcrawl threshold tuning

> **SMOKE TEST. NOT A MEASUREMENT.** This run used the synthetic fixture site, a stubbed answering LLM and judge, and a fake Jev. It only proves the harness runs end to end. Never quote these numbers.

- Thresholds chosen on 3 tuning tasks; the numbers below are the 2 held-out tasks only
- Chosen profile: `{"keep": 0.35, "link_floor": 0.1, "stop": 0.85}` (see results/tuned-profile.toml)

| System | Tasks | Errors | Accuracy | Gold page fetched | Pages fetched | LLM input tokens | Jev cost / task | Total cost / task | Wall time (s) |
|---|---|---|---|---|---|---|---|---|---|
| jevcrawl_jev (default) | 2 | 0 | 50.0% | 100.0% | 3.5 | 196 | $0.00009 | $0.00009 | 0.1 |
| jevcrawl_jev (tuned) | 2 | 0 | 50.0% | 100.0% | 3.5 | 196 | $0.00000 | $0.00000 | 0.1 |

How to read this table: accuracy counts an errored task as wrong; every other column is a mean over the tasks that did not error. LLM input tokens are the answering LLM's input plus, for `llm_nav`, the navigation calls. Total cost = answering/navigation LLM + Jev; the judge is excluded because it is the same for every system. Generated from the CSV next to this file; do not edit by hand.
