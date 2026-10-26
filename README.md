# Car Price Prediction API

[![CI](https://github.com/Add-it-ya/Fast_Api_Project/actions/workflows/ci.yml/badge.svg)](https://github.com/Add-it-ya/Fast_Api_Project/actions/workflows/ci.yml)

A FastAPI service that estimates the selling price of a used car in India from
twelve facts about it. Users sign in and ask for prices. Every prediction is
logged to PostgreSQL. Redis holds a cache and the rate-limit counters, and
Prometheus and Grafana show how the service and the model are doing. It runs
locally with Docker Compose. A Render blueprint is prepared but has not been
deployed yet.

The model is a random forest with a typical error of about 18%. The work here
is mostly in serving it: authentication, a non-blocking request path, a
batched prediction log, drift detection, and load tests whose raw results are
committed.

**Contents:** [Run it](#run-it) · [Use the API](#use-the-api) ·
[How a request is served](#how-a-request-is-served) ·
[Performance](#measured-performance) · [The model](#the-model) ·
[Monitoring](#monitoring) · [Container](#container) · [Tests](#tests) ·
[Design decisions](#design-decisions) · [Limitations](#known-limitations) ·
[Deployment](#deployment)

## Run it

```bash
git clone https://github.com/Add-it-ya/Fast_Api_Project.git
cd Fast_Api_Project
docker compose up --build
```

| | |
|---|---|
| API docs | http://localhost:8000/docs |
| Metrics | http://localhost:8000/metrics |
| Prometheus | http://localhost:9090 (alerts at `/alerts`) |
| Grafana | http://localhost:3000/d/car-price-api, provisioned, no login |

Compose supplies development settings. To run the API any other way, copy
`.env.example` to `.env` and fill it in: `API_KEY`, `JWT_SECRET_KEY` (at least
32 characters), `DATABASE_URL` and `REDIS_URL` have no defaults, and the app
refuses to start if one is missing, too short, or left as a placeholder.

## Use the API

```bash
curl -X POST localhost:8000/register -H 'content-type: application/json' \
  -d '{"username": "asha", "password": "a-long-password"}'

TOKEN=$(curl -s -X POST localhost:8000/login -H 'content-type: application/json' \
  -d '{"username": "asha", "password": "a-long-password"}' | jq -r .access_token)

curl -X POST localhost:8000/predict -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"company": "Maruti", "year": 2015, "owner": "Second", "fuel": "Petrol",
       "seller_type": "Individual", "transmission": "Automatic", "km_driven": 200000,
       "mileage_mpg": 55, "engine_cc": 1250, "max_power_bhp": 80, "torque_nm": 200,
       "seats": 5}'
```

```json
{"prediction_id": "0f8c...", "predicted_price": 409060.58, "currency": "INR",
 "model_version": 4, "cached": false}
```

| Endpoint | Who can call it |
|---|---|
| `POST /register`, `POST /login` | Anyone. Registration needs the operator key when `ALLOW_PUBLIC_REGISTRATION=false`. |
| `GET /me`, `POST /predict` | A signed-in user (bearer token, valid for 30 minutes) |
| `GET /predictions/history` | A signed-in user, who sees only their own predictions. Optional `company`, `year`, `limit`, `offset`. |
| `POST /predictions/{prediction_id}/actual` | An operator (`api-key` header). Records the real sale price once. |
| `GET /model/info`, `GET /model/drift` | Anyone. `?recompute=true` on drift needs the operator key. |
| `GET /model/performance` | A signed-in user |
| `GET /health`, `GET /ready` | Anyone. `/health` is liveness only; `/ready` checks the database, Redis and the model. |

Each user gets 60 requests a minute per endpoint, counted in Redis; callers
without a valid token are counted by IP. Compose raises the limit so the load
test measures the API rather than the limiter. Brands the model never trained on and
values outside sane ranges are rejected with a 422.

The twelve inputs are `company`, `year`, `owner`, `fuel`, `seller_type`,
`transmission`, `km_driven`, `mileage_mpg`, `engine_cc`, `max_power_bhp`,
`torque_nm` and `seats`. The allowed values are in `/docs`.

## How a request is served

```mermaid
flowchart TB
    client([Client])

    subgraph api["FastAPI, 4 uvicorn workers"]
        mw["Logging middleware<br/><i>request id, JSON access log</i>"]
        auth["Auth<br/><i>JWT for users, API key for operators</i>"]
        rl["Rate limiter"]
        routes["Routes"]
        services["Services<br/>PredictionService, AuthService,<br/>DriftMonitor, PredictionWriter"]
        repos["Repositories<br/><i>all SQL lives here</i>"]
    end

    redis[("Redis<br/><i>cache, rate limits</i>")]
    pg[("PostgreSQL 16<br/><i>users, predictions</i>")]
    model["scikit-learn pipeline<br/><i>asyncio.to_thread</i>"]
    prom["Prometheus"] --> graf["Grafana"]

    client --> mw --> auth --> rl --> routes --> services
    services -->|"cache hit, ~4 ms"| redis
    services -->|"miss, ~10 ms"| model
    services --> repos --> pg
    services -.->|"queued, written in batches"| repos
    rl --> redis
    api -->|"/metrics every 10 s"| prom
```

1. The token is checked from its signature and claims, with no database
   lookup.
2. The cache key is a hash of the features and the model version, so a
   retrained model never answers from its predecessor's cache.
3. On a miss, the model runs in a worker thread, so the event loop keeps
   serving other requests. It runs on one core per prediction; spreading one
   row across all cores was 7x slower.
4. The prediction row goes onto an in-memory queue, and the response returns
   with its `prediction_id`. A single consumer writes the queue to PostgreSQL in
   batches. A crash loses whatever is still queued, which is acceptable for an
   analytics log and would not be for anything the response promised.

If Redis goes down, predictions still work (every request becomes a model call)
and rate limiting is off until it returns. `/ready`, a metric and an alert all
show it.

## Measured performance

Model v4, one machine: Docker Desktop with 12 CPUs running the API, PostgreSQL,
Redis **and the load generator**. 1,000 requests per run, three runs per row,
median shown. Raw results are in [`benchmarks/`](benchmarks/README.md), and
`python scripts/benchmark_table.py <dir>` rebuilds each table from them.

Every request a different car, so every request runs the model:

| clients | 1 worker | 4 workers |
|---:|---:|---:|
| 1 | 116 req/s, p95 10.7 ms | 104 req/s, p95 12.1 ms |
| 10 | 96 req/s, p95 134 ms | 203 req/s, p95 105 ms |
| 25 | 97 req/s, p95 296 ms | **235 req/s**, p95 188 ms |
| 50 | 92 req/s, p95 638 ms | 157 req/s, p95 879 ms |
| 100 | 90 req/s, p95 1,240 ms | 98 req/s, p95 2,862 ms |

With a pool of 200 cars replayed (about 80% cache hits, a ratio the test sets
rather than one measured from real traffic), 4 workers serve **536 req/s at
10 clients with p95 49.6 ms**.

- One worker tops out near 95 model calls a second. Beyond that, extra clients
  only wait: at 10 clients the median latency is 10 divided by 96 per second.
- Four workers peak near 235 a second, 2.4x one worker.
- p95 stays under 100 ms for a single client when every request runs the
  model, and to 10 clients with 80% cache hits.
- Above about 25 clients throughput falls in every setup. Even `/health`, which
  does no work, drops from about 750 to 560 req/s at 100 clients, so past that
  point the numbers describe this machine as much as the service. Why
  `/predict` falls further than `/health` is not diagnosed yet.

The results from 1 September 2026 in `benchmarks/baseline` and
`benchmarks/optimised` measured three request-path fixes (asynchronous logging,
no user lookup per request, batched inserts) on the original 10-tree model, and
went from one worker to four at the same time. They are kept as the record of
that change and say nothing about the current model.

## The model

A scikit-learn pipeline (median imputation, one-hot encoding, random forest)
built from 6,926 listings, 5,540 for training and 1,386 held out, by
`python -m training.train_model`. The size of the
forest, 50 trees at depth 12, was picked by 5-fold cross-validation on the
training rows only (`python -m training.sweep`). The held-out rows were scored
once, afterwards:

| | model | predict the training mean | CV on training rows |
|---|---:|---:|---:|
| MAE | ₹71,407 | ₹275,313 | ₹81,127 |
| R² | 0.93 | -0.00 | 0.89 |
| MAPE | 17.80% | 106.58% | 18.02% |

Training writes a metadata file next to the model: version, data hash,
scikit-learn version, metrics, and the training distribution of every feature.
`/model/info` serves it, and startup warns if the installed scikit-learn does
not match the one that saved the model.

**Drift.** Every minute, a background task compares the newest 2,000 predictions
in the log with the training distribution using the Population Stability
Index, per feature. `scripts/drift_demo.py` sends 300 ordinary requests (worst
PSI 0.048, stable), then 1,200 skewed towards newer luxury cars (3.26,
significant).

**Live error.** An operator reports a car's real sale price against its
`prediction_id`. `/model/performance` and the `model_live_mae` metric compare
the live error with the training error.

Intended use, data and limitations: [docs/model_card.md](docs/model_card.md).

## Monitoring

Grafana's dashboard is provisioned by compose. It shows throughput, cache hit
ratio, latency split by cache outcome, inference time, 5xx rate, model version,
feature drift, live against training error, predictions by brand, and the
prediction-log writer. Prometheus loads eight alert rules from `alerts.yml`:
API down, 5xx over 1%, p95 over 500 ms, cache hit ratio under 20% while there
is traffic, drift over 0.25, prediction-log rows dropped, Redis failing, and
write-queue backlog. Nothing routes the alerts anywhere yet; they show in
Prometheus at `/alerts`.

Logs are JSON, one line per event, with a request id on every line. An incoming
`X-Request-ID` is kept and returned on the response. Set `LOG_FORMAT=text` for
readable output while developing. With more than one worker, set
`PROMETHEUS_MULTIPROC_DIR` so metrics are summed across workers instead of
reporting whichever worker answered the scrape; compose already does.

## Container

| | single stage (`ccc6807`) | multi-stage (now) |
|---|---:|---:|
| Base | `python:3.10` | `python:3.10-slim-bookworm`, pinned by digest |
| Unpacked size | 1,536 MB | **570 MB** |
| Compressed layers | 511 MB | **167 MB** |
| Runs as | root | uid 999 |
| Start to healthy | | 3.6 to 3.7 s, including migrations |

Both images were built on the same machine and measured the same way
([`benchmarks/image/`](benchmarks/image/)). CI reports the current one as 517 MB
using a different Docker setup. The container runs migrations, then uvicorn.
`/health` does not check the database or Redis, so a blip in either does not
make Docker restart a working API process.

## Tests

152 tests run against a real PostgreSQL and Redis. They create their own
`carprice_test` database and use Redis db 1, so they never touch development
data. With Python 3.10 to 3.12:

```bash
docker compose up -d postgres redis
pip install -r requirements-dev.txt
pytest --cov=app
```

Or entirely in Docker, with the image's `dev` target (the runtime image cannot
install packages, by design), once PostgreSQL and Redis are up as above. The
network is named after the folder you cloned into; `docker network ls` shows
it.

```bash
docker build --target dev -t carprice:dev .
docker run --rm --network fast_api_project_backend -v "$PWD:/work" -w /work \
  -e DATABASE_URL=postgresql+asyncpg://carprice:carprice@postgres:5432/carprice \
  -e REDIS_URL=redis://redis:6379 carprice:dev pytest
```

CI runs the suite on Python 3.10, 3.11 and 3.12 on every push, along with
`ruff check`, `ruff format --check`, `mypy app` and `bandit`, and builds the
image to check that it imports and does not run as root.

## Design decisions

Each one is written up in [docs/design.md](docs/design.md) with what was
measured and what it costs.

| Decision | Why | Cost |
|---|---|---|
| [JWT for users, API key for operators](docs/design.md#1-two-credentials-one-job-each) | Each check has one reason to exist | One shared operator key |
| [Inference in a thread, one core per prediction](docs/design.md#2-inference-in-a-thread-one-core-per-prediction) | `predict` would block the event loop | Concurrency, not parallelism |
| [A cache in Redis](docs/design.md#3-a-prediction-cache-in-redis) | Shared by all workers, keyed by model version | Benefit depends on a hit ratio nobody has measured |
| [Redis is optional](docs/design.md#4-redis-is-optional) | A cache outage should not be a full outage | No rate limiting during one |
| [PostgreSQL](docs/design.md#5-postgresql) | Concurrent writers, `EXPLAIN`, `COPY` | A server to run |
| [Batched prediction log](docs/design.md#6-the-prediction-log-is-written-in-batches) | No commit on the request path | Queued rows lost on a crash |
| [`/predict` trusts the token](docs/design.md#7-predict-trusts-the-token) | No database lookup per request | Deleted users keep access until expiry |
| [History index](docs/design.md#8-history-index-user_id-created_at-desc) | 62.8 ms to 0.196 ms for a 200,000-row account | 38 MB, updated on every insert |
| [Model chosen by cross-validation](docs/design.md#9-choosing-the-model-by-cross-validation) | The test rows stay unused until the end | Random split, not time-based |
| [Drift from the prediction log](docs/design.md#10-drift-by-psi-from-the-prediction-log) | One answer across workers and restarts | A count-based window |

## Known limitations

- **Throughput is one machine's.** With every request running the model,
  4 workers peak near 235 req/s, and the load generator shares the same cores.
- **The model is modest.** About 18% typical error, under 7,000 training rows,
  and 19 of 30 brands each under 1% of the data. No prediction intervals.
- **No time-based validation.** The split is random, and prices are historical
  and not adjusted for inflation.
- **The model file is a pickle** tied to scikit-learn 1.3.2; a different
  version may fail to load it. The version is checked at startup.
- **Prediction rows still queued when the process dies are lost**, and rows are
  dropped if the queue fills.
- **A deleted user keeps access until their token expires**, up to 30 minutes.
- **The drift window is a count, not a time span**, so with little traffic it
  can reach back weeks.
- **During a Redis outage nothing is rate limited.**
- **Not deployed**, single host, no horizontal scaling.

## Deployment

`render.yaml` declares the API, a managed PostgreSQL and a Redis-compatible Key
Value store on Render's free plan, and wires them together. **It has not been
deployed yet.** To deploy it: on Render choose New, then Blueprint, pick this
repository, and approve the three resources. The first boot runs the
migrations. The generated `API_KEY` in the web service's environment is the
operator key; registration is closed there, so creating a user needs it.

On the free plan the service sleeps after 15 minutes idle, so the first request
after that takes about a minute; the database expires after 30 days; and
there is 512 MB of RAM, so it runs one worker with a small connection pool.
Prometheus and Grafana stay local.
