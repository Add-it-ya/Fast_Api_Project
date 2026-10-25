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

## 📐 Architecture

Three layers. Routes call services, services call repositories, and only
repositories build queries — which is what keeps the separation real rather
than nominal.

```mermaid
flowchart TB
    client([Client])

    subgraph api["FastAPI · 4 uvicorn workers"]
        mw["LoggingMiddleware<br/><i>request id, JSON access log</i>"]
        auth["Auth<br/><i>JWT for users, API key for operators</i>"]
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
    services -->|"cache hit, ~4 ms"| redis
    services -->|"miss, ~10 ms"| model
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
- 🔐 **Authentication**: users sign in for a JWT; a separate API key guards operator actions (closed registration, reporting sale prices, forcing a drift recompute). bcrypt-hashed passwords (passlib)
- 🧠 **ML Model Prediction**: Trained model predicts used car prices
- 🚀 **Redis Caching**: Avoid redundant model computation, async client
- 📈 **Monitoring**: provisioned Grafana dashboard (11 panels), 8 Prometheus alert rules, custom ML metrics, JSON logs with request-id correlation
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
CONCURRENCY=10 TOTAL_REQUESTS=1000 python scripts/load_test.py
```

The script logs in, sends one warm-up request, then fires `TOTAL_REQUESTS`
`/predict` calls from `CONCURRENCY` concurrent clients and prints p50 / p95 /
p99 / max latency, with cache hits and misses reported separately.
`UNIQUE_VECTORS=0` makes every request a different car, so every one is a model
call. `TARGET=health` measures `/health` instead. Set `SERVER_WORKERS` so the
result file records how many workers the server ran.

---

## 📊 Measured performance

Model v4 on the current code. Docker Desktop with 12 CPUs, and **everything on
one machine**: the API, PostgreSQL, Redis and the load generator. 1,000
requests per run, three runs per row, median shown. The raw files are in
[`benchmarks/v4/`](benchmarks/v4/), and `python scripts/benchmark_table.py
benchmarks/v4/<dir>` rebuilds each table from them.

**Every request a different car**, so every request runs the model. This is
the conservative case, because real traffic rarely repeats all twelve features
exactly:

| clients | 1 worker req/s | 1 worker p95 | 4 workers req/s | 4 workers p95 |
|---:|---:|---:|---:|---:|
| 1 | 116 | 10.7 ms | 104 | 12.1 ms |
| 10 | 96 | 134 ms | 203 | 105 ms |
| 25 | 97 | 296 ms | **235** | 188 ms |
| 50 | 92 | 638 ms | 157 | 879 ms |
| 100 | 90 | 1,240 ms | 98 | 2,862 ms |

**A pool of 200 cars replayed**, which gives about 80% cache hits. That ratio
is set by the test, not measured from real traffic:

| clients | 1 worker req/s | 1 worker p95 | 4 workers req/s | 4 workers p95 |
|---:|---:|---:|---:|---:|
| 1 | 188 | 11.3 ms | 218 | 9.8 ms |
| 10 | 257 | 115 ms | **536** | 49.6 ms |
| 25 | 240 | 258 ms | 318 | 186 ms |
| 50 | 156 | 766 ms | 180 | 817 ms |
| 100 | 103 | 2,993 ms | 156 | 2,261 ms |

What that shows:

- **One worker tops out at about 95 model calls a second.** Past one client,
  extra clients only wait. At 10 clients the median latency (106 ms) is almost
  exactly 10 divided by 96 per second.
- **Four workers peak at about 235 a second, 2.4x one worker, not 4x.**
- **p95 stays under 100 ms** for one client when every request runs the model,
  and up to 10 clients when 80% are cache hits on 4 workers (49.6 ms).
- **Above about 25 clients throughput falls in every configuration.** Even
  `/health`, which does no work, drops from about 750 req/s at 10 clients to
  about 560 at 100 ([`benchmarks/v4/health-*`](benchmarks/v4/)). The load
  generator shares 12 cores with four workers, PostgreSQL and Redis, so at
  that point these numbers describe this machine as much as the service. Why
  `/predict` on 4 workers falls further than `/health`, to about 100 req/s at
  100 clients, has not been diagnosed. Too many inference threads competing for
  the cores is the first thing to check.
- At 100 clients on one worker, 1 request in 3,000 failed with a dropped
  connection.

### Earlier results, and what changed since

[`benchmarks/baseline/`](benchmarks/baseline/) and
[`benchmarks/optimised/`](benchmarks/optimised/) were recorded on
1 September 2026 against the original 10-tree, depth-5 forest, before any of
the current models existed. They show the combined effect of three request-path
fixes and of going from one worker to four at the same time: at 10 clients,
124 to 445 req/s. The fixes were not measured one at a time in committed runs.
Those files say nothing about the model served today. They are kept as the
record of that change.

The three fixes:

1. **Synchronous logging.** `logging` writes to stdout, and in an async service
   the calling thread is the event loop, so every log line stalled every request
   in flight. Records now go through a `QueueHandler` and a background thread
   does the writing, the access middleware was rewritten as pure ASGI rather than
   `BaseHTTPMiddleware`, and uvicorn's duplicate access log is off.
2. **A database round trip per authenticated request.** `get_current_user`
   loaded the user on every call. `/predict` now builds a `Principal` from the
   token claims instead, which is what a stateless JWT is for.
3. **A commit per prediction.** Prediction rows are an analytics record the
   caller never reads back, so they are queued and written by a single consumer
   as one multi-row `INSERT` per batch. Doing this as a per-request background
   task first was *worse* — every task opened its own session and fought over the
   connection pool — which is why it is batched rather than merely deferred.

Since then the model is served single-threaded, which took one prediction from
30 ms to 4.3 ms ([`benchmarks/inference/`](benchmarks/inference/)), and v4 has
half the trees of v3.

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

| | single stage (`ccc6807`) | multi-stage (now) |
|---|---:|---:|
| Base | `python:3.10` | `python:3.10-slim-bookworm`, pinned by digest |
| Unpacked size | 1,536 MB | **570 MB** |
| Compressed layers, what a pull downloads | 511 MB | **167 MB** |
| Runs as | root | **uid 999 (`app`)** |
| Cold start to healthy | | 3.6 to 3.7 s, including migrations |

**63% smaller unpacked, 67% smaller to download, and unprivileged.** Both images were built on the same
machine from clean exports of their commits and measured the same way
([`benchmarks/image/`](benchmarks/image/)). CI prints 517 MB for the current
image on every run. That is the same image measured by a differently configured
Docker. The base is pinned by digest, so a rebuild months from now produces the
same image rather than whatever the tag points at.

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

Latency is split by cache outcome because the two differ: at one client a hit
takes about 4 ms end to end and a miss about 10 ms
([benchmarks/cache](benchmarks/cache/)), so a single blended p95 mostly shows
the hit ratio.

### Custom metrics

| Metric | What it answers |
|---|---|
| `prediction_latency_seconds{cache}` | Is the cache doing its job, and what does a miss really cost? |
| `model_inference_seconds` | Is the model slow, or the service around it? |
| `predictions_total{company,cache}` | What is actually being asked about? |
| `model_feature_drift_psi{feature}` | Has traffic stopped looking like the training data? |
| `model_live_mae`, `model_training_mae` | Is the model still right, against reported outcomes, compared with how it trained? |
| `prediction_log_rows_written_total` / `_dropped_total` / `_queue_depth` | Is the async writer keeping up? |
| `model_version`, `model_sklearn_version_match` | Which model is loaded, and is it safe to load? |
| `redis_errors_total{operation}` | Is Redis down while the service carries on without it? |

### Alerts

Eight rules in `alerts.yml`, loaded by Prometheus and visible at
http://localhost:9090/alerts: API down, 5xx over 1%, prediction p95 over 500 ms,
cache hit ratio collapsed under 20%, feature drift PSI over 0.25, prediction log
rows lost, Redis failing, and write-queue backlog.

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

The forest's size was picked by 5-fold cross-validation on the training rows
(`training/sweep.py`), and the held-out rows were scored once afterwards, against
a baseline that predicts the training mean:

| | model | mean baseline | CV on training rows |
|---|---:|---:|---:|
| MAE | ₹71,407 | ₹275,313 | ₹81,127 |
| R² | **0.93** | -0.00 | 0.89 |
| MAPE | **17.80%** | 106.58% | 18.02% |

The baseline is there on purpose — an R² of 0.93 means little until you know
what predicting the average would have scored.

### Drift detection

`GET /model/drift` reports **Population Stability Index** per feature for the
newest 2,000 predictions in the prediction log, against the training
distribution, and exports `model_feature_drift_psi{feature=...}` to Prometheus.
Every worker reads the same rows, so they agree, and a background task
recomputes it every minute. Under 0.1 is stable, 0.1–0.25 moderate, above 0.25 significant.
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

`POST /predictions/{prediction_id}/actual` attaches a real sale price to a
logged prediction, using the `prediction_id` that `/predict` returned. It needs
the operator key and refuses to overwrite a price already recorded.
`GET /model/performance` reports live error over recent scored predictions next
to the training metrics. The same live error is exported as `model_live_mae`,
recomputed every minute in the background, next to `model_training_mae`.

### Version safety

The artifact is a pickle tied to the scikit-learn that wrote it — loading this
model under 1.9.0 raises rather than degrading. The version is recorded at
training time, checked at startup, and surfaced as `version_match` on
`/model/info`.

Full documentation, including limitations and intended use:
[docs/model_card.md](docs/model_card.md).

---

## 🔍 Query optimisation

`GET /predictions/history` returns the caller's own predictions, newest first:
`WHERE user_id = ? ORDER BY created_at DESC LIMIT 50`. With an index on
`user_id` alone, Postgres fetched all of a user's rows and sorted them. One
composite index serves the filter and the order:

```sql
CREATE INDEX ix_predictions_user_id_created_at
    ON predictions (user_id, created_at DESC);
```

Measured on 1,000,000 seeded rows, median of six runs:

| | before | after |
|---|---:|---:|
| Account with ~200,000 predictions | 62.8 ms, 23,390 buffers | **0.196 ms**, 53 buffers |
| Account with ~1,000 predictions | 2.03 ms, 991 buffers | **0.163 ms**, 53 buffers |

For an ordinary user the old index was already fast enough. The point of the
new one is that the cost no longer grows with how many predictions a user has
made.

Full plans: [docs/explain_before.txt](docs/explain_before.txt),
[docs/explain_after.txt](docs/explain_after.txt). Reasoning, costs and how to
reproduce it: [docs/query_optimisation.md](docs/query_optimisation.md).

### Loading the benchmark data

`scripts/seed_predictions.py` bulk loads with PostgreSQL `COPY`:

```bash
python scripts/seed_predictions.py --rows 1000000 --users 5 --truncate --compare
```

| method | rows/sec |
|---|---:|
| row-by-row `INSERT` | 2,248 |
| `COPY` | **33,463** |

**15x faster**, both timed in the same run on the same hardware with `--compare`
([`benchmarks/seed/`](benchmarks/seed/)). Generating rows in Python is timed
separately from the `COPY` itself.

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
| [Index column order](docs/adr/0005-composite-index-column-order.md) | Equality first, sort last, so one index serves both | 38 MB, maintained on every insert |
| [Token-only principal](docs/adr/0006-token-only-principal.md) | A database round trip on every request for what the token already says | Deleted users keep access until expiry |
| [PSI for drift](docs/adr/0007-psi-for-drift-detection.md) | Inputs shift before accuracy can be measured | A count-based window; needs 200+ samples |

Two of these were arrived at by measuring a worse alternative first: per-request
`BackgroundTasks` was slower than batching, and more uvicorn workers made
throughput *worse* until the connection pool was resized.

---

## 🚧 Known limitations

- **Throughput is one machine's.** With every request running the model, 4
  workers peak at about 235 req/s, and p95 is under 100 ms only for a single
  client. Above about 25 clients throughput falls, and the load generator
  shares the same cores, so the high-concurrency figures describe this host as
  much as the service.
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
- **The drift window is a count, not a time span.** It is the newest 2,000
  predictions, however old, so with little traffic it can stretch back weeks.
- **A Redis outage switches off rate limiting.** The cache and the limiter
  both fail open: predictions fall back to the model, and nothing is throttled
  until Redis returns. That keeps login and prediction available, at the cost
  of one layer of abuse protection. `/ready` reports it, `redis_errors_total`
  counts it, and the `RedisDegraded` alert fires on it.
- **Single-region, single-host.** No horizontal scaling story, no load balancer,
  no live deployment.

---

## 🚀 Deployment on Render

`render.yaml` is a Blueprint: it declares the API, a managed PostgreSQL and a
Key Value (Redis-compatible) cache, and wires the connection strings between
them. All three fit on Render's free plan.

1. Push this repository to GitHub.
2. On [Render](https://dashboard.render.com), choose **New > Blueprint** and
   pick the repository. Render reads `render.yaml` and lists the three
   resources it is about to create; approve them.
3. Wait for the first Docker build (roughly 5-10 minutes). The container runs
   `alembic upgrade head` before starting uvicorn, so the schema is created on
   the first boot.
4. Read the generated `API_KEY` from the web service's **Environment** tab.
   It is the operator credential: `/register` needs it, because
   `ALLOW_PUBLIC_REGISTRATION` is false on a publicly reachable instance, and
   so does reporting a sale price.

Interactive docs live at `https://<your-service>.onrender.com/docs`, and
`/ready` reports whether the database, the cache and the model are all up.

What the free plan costs you:

| Limit | Effect |
| --- | --- |
| Web service sleeps after 15 minutes idle | First request afterwards takes about a minute |
| Free PostgreSQL expires 30 days after creation | Recreate it, or point `DATABASE_URL` at another provider |
| Key Value holds nothing across restarts | Harmless - it is only a cache |
| 512 MB RAM, one instance | `WEB_CONCURRENCY=1`, small connection pool |

Grafana, Prometheus and the training stack are not part of the deployment; they
stay in `docker-compose.yml` for local use.

---

## 🤝 Contributing

Feel free to fork this repo, open issues, and submit pull requests.

---
