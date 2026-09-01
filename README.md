# 🚗 Car Price Prediction API

[![CI](https://github.com/Add-it-ya/Fast_Api_Project/actions/workflows/ci.yml/badge.svg)](https://github.com/Add-it-ya/Fast_Api_Project/actions/workflows/ci.yml)

This project is a **Machine Learning-powered API** built using **FastAPI** to predict the selling price of a used car based on its characteristics.

---

## 📦 Project Features

- ⚡ **Fully async stack**: `async def` routes, `redis.asyncio`, async SQLAlchemy 2.x, blocking ML inference offloaded with `asyncio.to_thread`
- 🗄️ **Persistence**: User accounts and prediction logs stored in PostgreSQL 16 via async SQLAlchemy 2.x, schema managed by Alembic
- 🔐 **Authentication**: JWT token auth + API key header, bcrypt-hashed passwords (passlib)
- 🧠 **ML Model Prediction**: Trained model predicts used car prices
- 🚀 **Redis Caching**: Avoid redundant model computation, async client
- 📈 **Monitoring Ready**: Prometheus metrics + Grafana dashboards
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
git clone https://github.com/your-username/fastapi-project.git
cd fastapi-project
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
- Prometheus UI: http://localhost:9090
- Grafana UI: http://localhost:3000

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

## 🚀 Deployment on Render (API only)

1. Push code to GitHub
2. Add render.yaml to the project root
3. Create a new Web Service on Render
4. Include environment variables

---

## 🤝 Contributing

Feel free to fork this repo, open issues, and submit pull requests.

---
