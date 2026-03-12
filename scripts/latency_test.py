import time
import statistics
import requests

URL = "http://localhost:8000/health"
N = 50

def main():
    times_ms = []

    # Warmup
    for _ in range(5):
        requests.get(URL, timeout=5)

    for i in range(N):
        t0 = time.perf_counter()
        r = requests.get(URL, timeout=5)
        t1 = time.perf_counter()

        if r.status_code != 200:
            raise RuntimeError(f"Request failed at i={i}: {r.status_code} {r.text}")

        times_ms.append((t1 - t0) * 1000)

    print(f"URL: {URL}")
    print(f"Requests: {N}")
    print(f"Avg (ms): {statistics.mean(times_ms):.2f}")
    print(f"P50 (ms): {statistics.median(times_ms):.2f}")
    print(f"P95 (ms): {statistics.quantiles(times_ms, n=20)[18]:.2f}")
    print(f"Min (ms): {min(times_ms):.2f}")
    print(f"Max (ms): {max(times_ms):.2f}")

if __name__ == "__main__":
    main()