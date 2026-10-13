# jevcrawl benchmark (smoke test)

> **SMOKE TEST. NOT A MEASUREMENT.** This run used the synthetic fixture site, a stubbed answering LLM and judge, and a fake Jev. It only proves the harness runs end to end. Never quote these numbers.

- Run `smoke-20261011-165430-0a46`: 5 synthetic tasks on the fixture help-center site

| System | Tasks | Errors | Accuracy | Gold page fetched | Pages fetched | LLM input tokens | Jev cost / task | Total cost / task | Wall time (s) |
|---|---|---|---|---|---|---|---|---|---|
| bfs | 5 | 0 | 80.0% | 100.0% | 5.0 | 562 | $0.00000 | $0.00000 | 0.0 |
| llm_nav | 5 | 0 | 20.0% | 20.0% | 1.2 | 484 | $0.00000 | $0.00000 | 0.0 |
| jevcrawl_jev | 5 | 0 | 80.0% | 100.0% | 3.8 | 178 | $0.00009 | $0.00009 | 0.1 |
| jevcrawl_local | 5 | 0 | 80.0% | 100.0% | 3.8 | 178 | $0.00009 | $0.00009 | 0.1 |

How to read this table: accuracy counts an errored task as wrong; every other column is a mean over the tasks that did not error. LLM input tokens are the answering LLM's input plus, for `llm_nav`, the navigation calls. Total cost = answering/navigation LLM + Jev; the judge is excluded because it is the same for every system. Generated from the CSV next to this file; do not edit by hand.
