# Benchmarks

Raw results behind every performance figure in the README and docs. Each
directory says what it measured. All of it ran on one Docker Desktop machine
with 12 CPUs, with the load generator on the same host as the service.

| Directory | What it measured | Code and model |
|---|---|---|
| [`v4/`](v4/) | `/predict` and `/health` under load at 1 and 4 workers: every request distinct (`predict-unique-*`), a replayed pool of 200 cars (`predict-pool200-*`), and `/health` (`health-*`) | commits `1851772` / `6787563`, which serve identical application code; model v4 |
| [`cache/`](cache/) | Cache hit against miss latency, 4 workers | `16cfaef`, model v4 |
| [`inference/`](inference/) | One prediction with `n_jobs=-1` against `n_jobs=1`, outside the API | models v3 and v4 |
| [`image/`](image/) | Image size before and after the multi-stage build, and cold start | see the files |
| [`seed/`](seed/) | `COPY` against row-by-row `INSERT` for the seed script | see the file |
| [`baseline/`](baseline/), [`optimised/`](optimised/) | `/predict` before and after the request-path fixes, 1 September 2026 | the original 10-tree, depth-5 model, at 1 and then 4 workers. **Not the current model.** Kept as the record of that change. |

Load-test files from `v4/` onward record the git commit, server worker count
and model version in their `config` block. The older ones do not, which is how
it went unnoticed for a while that they described a different model.

Rebuild a table from any directory of load-test files:

```bash
python scripts/benchmark_table.py benchmarks/v4/predict-unique-4-workers
```
