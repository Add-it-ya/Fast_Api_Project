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

Docker Desktop, 12 CPUs available, single uvicorn worker, all services on one
host. 1000 requests per run, ~58% cache miss rate. Raw runs are in
`benchmarks/`.

| Concurrency | Throughput | p50 | p95 | p99 |
|------------:|-----------:|----:|----:|----:|
| 1   |  95 req/s |  11 ms |   14 ms |   17 ms |
| 10  | 125 req/s |  77 ms |  114 ms |  133 ms |
| 25  | 111 req/s | 211 ms |  368 ms |  588 ms |
| 50  | 120 req/s | 394 ms |  548 ms |  995 ms |
| 100 | 112 req/s | 740 ms | 1955 ms | 2732 ms |

Throughput saturates near 120 req/s, so past roughly 10 concurrent clients the
extra latency is queueing rather than work — p95 stays under 100 ms up to about
8-way concurrency and degrades linearly after that.

Two measured contributors to the ceiling, both still open:

- **Per-request logging is synchronous.** Suppressing the logging middleware and
  the uvicorn access log took a no-op endpoint from 156 to 678 req/s on the same
  host.
- **Every prediction makes four network round trips** before responding: a user
  lookup for the bearer token and the prediction insert against PostgreSQL, plus
  a rate-limit counter and a cache read against Redis.

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
