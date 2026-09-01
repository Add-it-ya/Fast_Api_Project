# 0001. Offload inference with asyncio.to_thread

**Status:** Accepted

## Context

scikit-learn's `predict` is synchronous and CPU-bound. Called directly from an
`async def` route it holds the event loop for its whole duration, so every other
request in flight waits — the service stops being concurrent at precisely the
moment concurrency matters.

The original code sidestepped this accidentally: its routes were `def`, so
FastAPI ran them in a threadpool. Converting to `async def` for the database
work removed that accident and made the problem real.

## Decision

Run inference through `asyncio.to_thread`, which hands it to the default
executor and leaves the loop free.

Alternatives considered:

- **A process pool.** Sidesteps the GIL, which matters for pure-Python CPU work.
  scikit-learn's hot paths release the GIL in native code already, so a thread
  gets most of the benefit without paying to pickle a DataFrame across a process
  boundary on every call.
- **A separate inference service.** The right answer at a size this project is
  nowhere near. It buys independent scaling and adds a network hop, a
  deployment, and a failure mode — for a model that answers in single-digit
  milliseconds.

## Consequences

Inference no longer blocks the loop. Concurrent predictions overlap, which
`tests/test_concurrency.py` asserts by comparing serial against concurrent
timings in the same run — it fails if inference ever returns to the loop.

The executor's thread count now bounds inference throughput. It is not tuned;
if inference ever dominates, that is the first knob.

The GIL still serialises the Python-level parts of the call. This buys
concurrency, not parallelism, and would stop being enough for a heavier model.
