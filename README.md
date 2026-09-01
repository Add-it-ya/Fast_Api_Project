# 🚗 Car Price Prediction API

[![CI](https://github.com/Add-it-ya/Fast_Api_Project/actions/workflows/ci.yml/badge.svg)](https://github.com/Add-it-ya/Fast_Api_Project/actions/workflows/ci.yml)

A **FastAPI** service that predicts the selling price of a used car from twelve
features, built to be measured rather than described: every performance claim
below comes from a script in this repository and can be reproduced.

**Jump to:** [Architecture](#-architecture) · [Quickstart](#-getting-started-local)
· [Performance](#-measured-performance) · [Container](#-container)
· [Observability](#-observability) · [Model lifecycle](#-model-lifecycle)
· [Query optimisation](#-query-optimisation) · [Tests](#-running-the-tests)
· [Design decisions](#-design-decisions) · [Limitations](#-known-limitations)

---

## 🏗️ Architecture

Three layers. Routes call services, services call repositories, and only
repositories build queries — which is what keeps the separation real rather
than nominal.

```mermaid
flowchart TB
    client([Client])

    subgraph api["FastAPI · 4 uvicorn workers"]
        mw["LoggingMiddleware<br/><i>request id, JSON access log</i>"]
        auth["Auth<br/><i>JWT bearer + API key</i>"]
        rl["Rate limiter"]
        routes["<b>API layer</b><br/>routes_predict · routes_model · routes_auth"]
        services["<b>Business layer</b><br/>PredictionService · AuthService<br/>DriftMonitor · PredictionWriter"]
        repos["<b>Data access layer</b><br/>PredictionRepository · UserRepository"]
    end

    redis[("Redis<br/><i>cache · rate limit</i>")]
    pg[("PostgreSQL 16<br/><i>users · predictions</i>")]
    model["scikit-learn pipeline<br/><i>asyncio.to_thread</i>"]
    prom["Prometheus"]
    graf["Grafana"]

    client --> mw --> auth --> rl --> routes --> services
    services -->|"cache hit ~8ms"| redis
    services -->|"miss ~240ms"| model
    services --> repos --> pg
    services -.->|"queued, batched"| repos
    rl --> redis
    api -->|"/metrics every 10s"| prom --> graf

    classDef store fill:#e8f0fe,stroke:#4285f4
    classDef ml fill:#e6f4ea,stroke:#34a853
    class redis,pg store
    class model ml
```

The dotted edge is the prediction log: rows are queued and written in batches
after the response is sent, so the commit is off the request path
([ADR 0004](docs/adr/0004-batch-the-prediction-log.md)).

---

## 📦 Project Features

- ⚡ **Fully async stack**: `async def` routes, `redis.asyncio`, async SQLAlchemy 2.x, blocking ML inference offloaded with `asyncio.to_thread`
- 🗄️ **Persistence**: User accounts and prediction logs stored in PostgreSQL 16 via async SQLAlchemy 2.x, schema managed by Alembic
- 🔐 **Authentication**: JWT token auth + API key header, bcrypt-hashed passwords (passlib)
- 🧠 **ML Model Prediction**: Trained model predicts used car prices
- 🚀 **Redis Caching**: Avoid redundant model computation, async client
- 📈 **Monitoring**: provisioned Grafana dashboard (11 panels), 7 Prometheus alert rules, custom ML metrics, JSON logs with request-id correlation
- 🐳 **Dockerized Setup**: Simplified deployment with Docker Compose
- ☁️ **Cloud Deployment**: Easily deploy to [Render](https://render.com)
- 🧪 **Load test included**: `scripts/load_test.py` measures p50/p95/p99 latency under configurable concurrency
- ✅ **Tested**: 101 tests against real PostgreSQL and Redis, including a regression test that fails if inference ever blocks the event loop again
- 🔬 **Model lifecycle**: versioned artifact with recorded metrics and data hash, PSI drift detection, and live scoring against reported sale prices

---

## 🧠 Model Input Variables

The prediction model expects the following input features:

| Feature           | Description                          | Example         |
|------------------|--------------------------------------|-----------------|
| `company`         | Brand of the car                     | `"Maruti"`      |
| `year`            | Year of manufacturing                | `2015`          |
| `owner`           | Number of previous owners            | `"Second"`      |
| `fuel`            | Fuel type                            | `"Petrol"`      |
| `seller_type`     | Individual or Dealer                 | `"Individual"`  |
| `transmission`    | Transmission type                    | `"Automatic"`   |
| `km_driven`       | Kilometers driven                    | `200000`        |
| `mileage_mpg`     | Mileage in miles per gallon          | `55`            |
| `engine_cc`       | Engine capacity in cc                | `1250`          |
| `max_power_bhp`   | Maximum power in BHP                 | `80`            |
| `torque_nm`       | Torque in Newton meters              | `200`           |
| `seats`           | Number of seats                      | `5`             |

---

## 🚀 Getting Started (Local)

### 1. Clone the Repository

```bash
git clone https://github.com/Add-it-ya/Fast_Api_Project.git
cd Fast_Api_Project
```

### 2. Set Environment Variables

Copy `.env.example` to `.env` and fill it in. There are no fallback defaults for
these — the app fails to start if any is missing, too short, or left as a
placeholder, so a misconfigured deployment cannot come up with a guessable secret.

```bash
JWT_SECRET_KEY=   # min 32 chars: python -c "import secrets; print(secrets.token_urlsafe(48))"
API_KEY=          # min 8 chars
DATABASE_URL=postgresql+asyncpg://carprice:carprice@localhost:5432/carprice
REDIS_URL=redis://localhost:6379
```

`docker-compose.yml` already supplies development values, so step 3 works without a `.env`.

### 3. Build and Run via Docker

```bash
docker-compose up --build
```

### 4. Access Interfaces

- FastAPI Docs: http://localhost:8000/docs
- FastAPI Metrics: http://localhost:8000/metrics
- Prometheus UI: http://localhost:9090 (alerts at `/alerts`)
- Grafana UI: http://localhost:3000 — the dashboard is
  [provisioned automatically](#-observability), no login needed

### 5. Run the load test

```bash
# in another shell, with the API running
CONCURRENCY=100 TOTAL_REQUESTS=1000 python scripts/load_test.py
```

The script logs in, warms the cache, then fires `TOTAL_REQUESTS` `/predict`
calls across `CONCURRENCY` workers and prints p50 / p95 / p99 / max latency.

Each run generates a fresh pool of feature vectors, so a repeat run is not
quietly measuring a Redis cache that the previous run warmed. Cache hits and
misses are reported separately — a p95 made up of cache hits says nothing about
the inference path. Pin `SEED` to reproduce a run exactly.

---

## 📊 Measured performance

Docker Desktop, 12 CPUs, all services on one host. 1000 requests per run,
~55% cache miss rate. Raw runs are committed under `benchmarks/`.

`baseline` is a single uvicorn worker before any tuning; `optimised` is the
current code on 4 workers. Optimised figures are the median of three runs;
baseline figures are a single run each, so treat those as indicative.

| Concurrency | baseline p95 | optimised p95 | baseline rps | optimised rps |
|------------:|-------------:|--------------:|-------------:|--------------:|
| 1   |   14 ms |     **9 ms** |  95 |     **173** |
| 10  |  114 ms |    **47 ms** | 124 |     **445** |
| 25  |  368 ms |   **166 ms** | 111 |     **337** |
| 50  |  548 ms |       790 ms | 120 |     **187** |
| 100 | 1955 ms |      1415 ms | 112 |     **187** |

Throughput improved at every level, by 1.6x to 3.6x. p95 improved everywhere
except 50-way, where the single baseline run looks optimistic next to its own
neighbours and the comparison is not trustworthy.

**Sub-100 ms p95 holds to roughly 10-15 concurrent clients.** It does not hold at
100 — that needs more than one host. On this hardware even `/health`, which
touches nothing, saturates around 530 req/s at 100-way concurrency, so the
remaining latency there is queueing that no application-level change removes.

### What the optimisation actually was

Three bottlenecks, each measured before being changed:

1. **Synchronous logging.** `logging` writes to stdout, and in an async service
   the calling thread is the event loop, so every log line stalled every request
   in flight. Records now go through a `QueueHandler` and a background thread
   does the writing, the access middleware was rewritten as pure ASGI rather than
   `BaseHTTPMiddleware`, and uvicorn's duplicate access log is off. On its own
   this took `/health` from 156 to 739 req/s.
2. **A database round trip per authenticated request.** `get_current_user`
   loaded the user on every call. `/predict` now builds a `Principal` from the
   token claims instead, which is what a stateless JWT is for. Measured cost of
   that lookup: ~2.2 ms per request.
3. **A commit per prediction.** Prediction rows are an analytics record the
   caller never reads back, so they are queued and written by a single consumer
   as one multi-row `INSERT` per batch. Doing this as a per-request background
   task first was *worse* — every task opened its own session and fought over the
   connection pool — which is why it is batched rather than merely deferred.

Rows still queued when the process dies are lost. That is the trade for taking
the write off the request path, and it is only acceptable because nothing tells
the caller the row was saved.

Connection pools are sized per worker: `WEB_CONCURRENCY x (DB_POOL_SIZE +
DB_MAX_OVERFLOW)` must stay under PostgreSQL's `max_connections`. Exceeding it
surfaces as `asyncpg.TooManyConnectionsError` under load, not at startup.

---

## 🐳 Container

Multi-stage build. Compilers and headers live in the build stage and never
reach the runtime image, which carries the virtualenv and one system library —
`libgomp1`, the OpenMP runtime scikit-learn links against and the one thing the
slim base is missing that the model actually needs.

| | before | after |
|---|---:|---:|
| Base | `python:3.10` | `python:3.10-slim-bookworm`, pinned by digest |
| Image size (Docker Desktop) | 2.21 GB | **831 MB** |
| Image size (single platform, CI) | — | **517 MB** |
| Runs as | root | **uid 999 (`app`)** |
| Cold start to healthy | — | ~3.9 s, including migrations |

**62% smaller and unprivileged.** Both size figures are real and measured the
same way on each host: Docker Desktop's buildx attaches multi-platform
attestation manifests, so it reports more than the single-platform image CI
actually builds. 517 MB is what would be deployed. The base is pinned by digest
so a rebuild months from now produces the same image rather than whatever the
tag points at.

The runtime user cannot write to the virtualenv, which is the point — so test
tooling gets its own target instead of the production image being loosened to
accommodate it:

```bash
docker build --target dev -t carprice:dev .
docker run --rm carprice:dev pytest
```

Compose wires a healthcheck to `/health`. That endpoint is liveness only and
deliberately does not check PostgreSQL or Redis — otherwise a database blip
would make Docker restart an API process that is working perfectly. `/ready` is
the one that checks dependencies.

---

## 📈 Observability

`docker-compose up` provisions a Grafana dashboard automatically — datasource,
dashboard and alert rules are all committed, so there is nothing to click.

Open **http://localhost:3000/d/car-price-api** (anonymous viewing is on for the
local stack).

> To capture the dashboard for this README: bring the stack up, generate traffic
> with `CONCURRENCY=10 TOTAL_REQUESTS=1200 python scripts/load_test.py`, then
> open `http://localhost:3000/d/car-price-api?kiosk` in a window at least
> 1600 px wide — narrower and Grafana renders the plot areas blank. Save it to
> `docs/images/dashboard.png`.

**11 panels across two rows.** Service: throughput, cache hit ratio, prediction
p95, 5xx rate, model version. Model health: prediction latency split by cache
outcome, model inference time on its own, feature drift PSI, live error against
training, predictions by company, and the prediction log writer.

Splitting latency by cache outcome is the panel that earns its place — a cache
hit runs around 8 ms and a miss around 240 ms, so a single blended p95 mostly
measures the hit ratio rather than anything about the service.

### Custom metrics

| Metric | What it answers |
|---|---|
| `prediction_latency_seconds{cache}` | Is the cache doing its job, and what does a miss really cost? |
| `model_inference_seconds` | Is the model slow, or the service around it? |
| `predictions_total{company,cache}` | What is actually being asked about? |
| `model_feature_drift_psi{feature}` | Has traffic stopped looking like the training data? |
| `model_live_mae` | Is the model still right, against reported outcomes? |
| `prediction_log_rows_written_total` / `_dropped_total` / `_queue_depth` | Is the async writer keeping up? |
| `model_version`, `model_sklearn_version_match` | Which model is loaded, and is it safe to load? |

### Alerts

Seven rules in `alerts.yml`, loaded by Prometheus and visible at
http://localhost:9090/alerts: API down, 5xx over 1%, prediction p95 over 500 ms,
cache hit ratio collapsed under 20%, feature drift PSI over 0.25, prediction log
shedding, and write-queue backlog.

### Structured logs

Logs are JSON with a request id on every line. An inbound `X-Request-ID` is
honoured so a trace survives across services, and it comes back on the response
so a caller can quote it:

```json
{"ts": "2026-09-01T18:22:26Z", "level": "INFO", "logger": "api.access",
 "request_id": "my-trace-abc123", "message": "GET /health 200 0.2ms",
 "method": "GET", "path": "/health", "status": 200, "duration_ms": 0.15}
```

Set `LOG_FORMAT=text` for human-readable output while developing.

### Metrics across workers

With `WEB_CONCURRENCY > 1` each worker keeps its own registry, so a scrape would
report whichever worker happened to answer and counters would appear to jump
between processes. `PROMETHEUS_MULTIPROC_DIR` makes `prometheus_client`
aggregate across them; the entrypoint clears it at startup so stale files from a
previous run are not counted. **Set it whenever you run more than one worker.**

---

## 🧠 Model lifecycle

`python -m training.train_model` writes the fitted pipeline **and** a metadata
file recording what produced it: version, training date, data hash, the
scikit-learn version it was pickled with, validation metrics, and the training
feature distribution. `GET /model/info` serves that at runtime.

Held-out metrics, against a baseline that predicts the training mean:

| | model | mean baseline |
|---|---:|---:|
| MAE | ₹71,366 | ₹275,313 |
| R² | **0.93** | -0.00 |
| MAPE | **17.76%** | 106.58% |

The baseline is there on purpose — an R² of 0.93 means little until you know
what predicting the average would have scored.

### Drift detection

`GET /model/drift` reports **Population Stability Index** per feature against
the training distribution, and exports `model_feature_drift_psi{feature=...}` to
Prometheus. Under 0.1 is stable, 0.1–0.25 moderate, above 0.25 significant.
Input drift is the earliest available warning: accuracy cannot be measured until
real outcomes come back, but the input distribution shifts immediately.

`scripts/drift_demo.py` demonstrates it — one batch drawn from the training
distribution, then one skewed towards newer luxury cars:

```
after in-distribution traffic:
  window 300 samples, worst PSI 0.0479 (stable)

after shifted traffic:
  window 1500 samples, worst PSI 3.2609 (significant)
  company            3.261  significant
  transmission       2.880  significant
  km_driven          2.847  significant
```

### Scoring against reality

`POST /predictions/{id}/actual` attaches a real sale price to a logged
prediction; `GET /model/performance` reports live error over recent scored
predictions next to the training metrics, and exports `model_live_mae`. This is
what turns the prediction log from an archive into a monitoring asset.

### Version safety

The artifact is a pickle tied to the scikit-learn that wrote it — loading this
model under 1.9.0 raises rather than degrading. The version is recorded at
training time, checked at startup, and surfaced as `version_match` on
`/model/info`.

Full documentation, including limitations and intended use:
[docs/model_card.md](docs/model_card.md).

---

## 🗂️ Query optimisation

`GET /predictions/history` filters by company and model year and returns the
newest first. Against 1,010,000 seeded rows (187 MB), with only a primary key on
the table, that plan is a parallel sequential scan discarding **336,281 rows per
worker** and then sorting the survivors.

One composite index fixes both halves:

```sql
CREATE INDEX ix_predictions_company_year_created_at
    ON predictions (company, year, created_at DESC);
```

| | before | after |
|---|---:|---:|
| Execution time | 26.1 ms | **0.177 ms** |
| Buffers touched | 20,476 | **53** |
| Plan | Parallel Seq Scan + top-N heapsort | Index Scan, no sort |

**147x faster.** The equality columns lead so a b-tree descent lands on the
matching range, and `created_at DESC` trails so the rows come back already in the
requested order — which is what removes the sort node, not just the table scan.

Full plans: [docs/explain_before.txt](docs/explain_before.txt),
[docs/explain_after.txt](docs/explain_after.txt). Reasoning, costs and how to
reproduce it: [docs/query_optimisation.md](docs/query_optimisation.md).

### Loading the benchmark data

`scripts/seed_predictions.py` bulk loads with PostgreSQL `COPY`:

```bash
python scripts/seed_predictions.py --rows 1000000 --truncate --compare
```

| method | rows/sec |
|---|---:|
| row-by-row `INSERT` | 1,346 |
| `COPY` | **48,040** |

**36x faster.** `--compare` times both in the same run on the same hardware, and
row generation in Python is timed separately from the COPY itself.

---

## 🧪 Running the tests

The suite runs against a real PostgreSQL and Redis rather than mocks. It creates
its own `carprice_test` database and uses Redis db 1, so it never touches
development data.

```bash
docker-compose up -d postgres redis
pip install -r requirements-dev.txt
pytest --cov=app --cov-report=term-missing
```

CI runs the same suite on Python 3.10, 3.11 and 3.12, plus `ruff`, `mypy` and
`bandit`, on every push and pull request.

| Check | Command |
|---|---|
| Lint | `ruff check .` |
| Format | `ruff format --check .` |
| Types | `mypy app` |
| Security | `bandit -c pyproject.toml -r app` |
| Tests | `pytest --cov=app` |

---

## 🧭 Design decisions

Each decision below is recorded in [`docs/adr/`](docs/adr/) with what was
measured and what it cost.

| Decision | Why | Cost |
|---|---|---|
| [Offload inference to a thread](docs/adr/0001-offload-inference-to-a-thread.md) | Sync `predict` on an async route blocks every request in flight | Bounded by executor threads; concurrency, not parallelism |
| [Redis, not in-process cache](docs/adr/0002-redis-for-prediction-cache.md) | 4 workers would each keep their own copy | Redis on the read path |
| [PostgreSQL over SQLite](docs/adr/0003-postgresql-over-sqlite.md) | Concurrent writers, real query planner, `COPY` | An operational dependency |
| [Batch the prediction log](docs/adr/0004-batch-the-prediction-log.md) | A commit per request on the critical path | Queued rows are lost on crash |
| [Index column order](docs/adr/0005-composite-index-column-order.md) | Equality first, sort last, so one index serves both | 39 MB, maintained on every insert |
| [Token-only principal](docs/adr/0006-token-only-principal.md) | A database lookup on every request cost ~2.2 ms | Deleted users keep access until expiry |
| [PSI for drift](docs/adr/0007-psi-for-drift-detection.md) | Inputs shift before accuracy can be measured | Per-worker windows; needs 200+ samples |

Two of these were arrived at by measuring a worse alternative first: per-request
`BackgroundTasks` was slower than batching, and more uvicorn workers made
throughput *worse* until the connection pool was resized.

---

## ⚠️ Known limitations

- **Sub-100 ms p95 does not hold at 100-way concurrency.** It holds to roughly
  10–15 clients. On one host even `/health`, which touches nothing, saturates
  around 530 req/s — past that the latency is queueing, and no application
  change removes it. Getting further needs more than one machine.
- **The model is modest.** MAPE is ~18%: a typical prediction is off by about a
  fifth of the true price. Useful as a starting point, not as a final number.
  Under 7,000 training rows for a 32-brand market, and 19 of 30 brands are under
  1% of the data.
- **No temporal validation.** The train/test split is random, not chronological,
  so the metrics say nothing about how the model holds up as the market moves.
  Prices are historical and unadjusted for inflation.
- **Point estimates only.** No prediction interval, so the API cannot express
  how confident it is about any individual car.
- **The artifact is a pickle** tied to scikit-learn 1.3.2. Loading it under
  1.9.0 raises rather than degrading. The version is recorded and checked at
  startup, and surfaced as `version_match` on `/model/info`, but the underlying
  fragility remains.
- **Queued prediction rows are lost if the process dies**, and are shed when the
  queue is full. Acceptable for an analytics log, not for anything the caller is
  told was saved.
- **A deleted user keeps access until their token expires** (up to 30 minutes) —
  inherent to stateless JWT.
- **Drift state is per-worker and in memory.** Prometheus sees one series per
  worker; alert on the maximum, not on a single number.
- **A Redis outage fails predictions** rather than degrading to a direct model
  call. The cache is on the read path and is not currently optional.
- **Single-region, single-host.** No horizontal scaling story, no load balancer,
  no live deployment.

---

## 🚀 Deployment on Render (API only)

1. Push code to GitHub
2. Add render.yaml to the project root
3. Create a new Web Service on Render
4. Include environment variables

---

## 🤝 Contributing

Feel free to fork this repo, open issues, and submit pull requests.

---
