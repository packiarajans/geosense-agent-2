# test_load.py
# Purpose: simple load test of a RUNNING backend (local or Cloud Run). Not a pytest test (no test_ functions).
# Run:  set BACKEND_URL=https://geosense-backend-xxxxx-uc.a.run.app   (PowerShell: $env:BACKEND_URL=...)
#       python tests/test_load.py
import os
import statistics
import time

import httpx

BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:8000').rstrip('/')
CHENNAI = {'latitude': 13.0827, 'longitude': 80.2707}


def run_load_test(n_requests=20):
    latencies, errors = [], 0
    with httpx.Client() as client:
        for _ in range(n_requests):
            start = time.time()
            try:
                client.get(f'{BACKEND_URL}/health', timeout=30).raise_for_status()
                client.post(f'{BACKEND_URL}/tools/get_climate_risk', json=CHENNAI, timeout=30).raise_for_status()
            except Exception:
                errors += 1
            latencies.append(time.time() - start)
    latencies.sort()
    p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
    print(f'Target: {BACKEND_URL}')
    print(f'Requests: {n_requests} (each = /health + /tools/get_climate_risk), Errors: {errors}')
    print(f'Avg latency: {statistics.mean(latencies):.2f}s   p95: {p95:.2f}s   max: {latencies[-1]:.2f}s')
    print('PASS' if errors == 0 and statistics.mean(latencies) < 5 else 'CHECK: errors or avg latency >= 5s')
    print('Note: the first request after idle can be a Cloud Run cold start (min-instances 0).')


if __name__ == '__main__':
    run_load_test()
