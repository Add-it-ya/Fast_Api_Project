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
- ✅ **Tested**: 56 tests against real PostgreSQL and Redis, including a regression test that fails if inference ever blocks the event loop again

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
