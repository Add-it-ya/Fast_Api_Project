"""Summarise committed load-test results as a markdown table.

Groups the result files in each directory by concurrency and reports the
median across runs of each figure, so every number quoted from benchmarks/
can be regenerated from the files themselves.

    python scripts/benchmark_table.py benchmarks/v4/predict-1-worker benchmarks/v4/predict-4-workers
"""

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def median_of(results: list[dict], *keys: str) -> float:
    values = []
    for result in results:
        value = result
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, int | float):
            values.append(value)
    return statistics.median(values) if values else float('nan')


def summarise(directory: Path) -> dict[int, dict]:
    runs: dict[int, list[dict]] = defaultdict(list)
    for path in sorted(directory.glob('*.json')):
        result = json.loads(path.read_text())
        runs[result['config']['concurrency']].append(result)

    return {
        concurrency: {
            'runs': len(results),
            'rps': median_of(results, 'throughput_rps'),
            'p50': median_of(results, 'overall', 'p50_ms'),
            'p95': median_of(results, 'overall', 'p95_ms'),
            'p99': median_of(results, 'overall', 'p99_ms'),
            'hit_ratio': median_of(results, 'cache_hit_ratio'),
            'failed': sum(r['failed'] for r in results),
        }
        for concurrency, results in sorted(runs.items())
    }


def main() -> None:
    for name in sys.argv[1:]:
        directory = Path(name)
        rows = summarise(directory)
        config = json.loads(next(directory.glob('*.json')).read_text())['config']
        target = config.get('target', 'predict')
        print(f'\n{directory}  (target {target}, workers {config.get("server_workers")}, ', end='')
        print(f'model v{config.get("model_version")}, git {config.get("git_sha")})\n')
        print('| clients | runs | req/s | p50 | p95 | p99 | cache hits | failed |')
        print('|---:|---:|---:|---:|---:|---:|---:|---:|')
        for concurrency, row in rows.items():
            latency = f'{row["p50"]:.1f} ms | {row["p95"]:.1f} ms | {row["p99"]:.1f} ms'
            print(
                f'| {concurrency} | {row["runs"]} | {row["rps"]:.0f} | {latency} '
                f'| {row["hit_ratio"]:.0%} | {row["failed"]} |'
            )


if __name__ == '__main__':
    main()
