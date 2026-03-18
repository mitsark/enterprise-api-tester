import concurrent.futures
import json
import os
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

CONFIG_PATH = "config.json"
REPORT_PATH = os.path.join("reports", "test-report.md")

# Defaults
DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_MAX_WORKERS = 5
DEFAULT_RETRIES = 0
DEFAULT_MAX_RPS = 0.0  # 0 means no limit
DEBUG_SNIPPET_CHARS = 200


class RateLimiter:
    """The 'Bouncer' that ensures we don't exceed the max requests per second."""
    def __init__(self, max_rps: float):
        self.max_rps = max_rps
        self.lock = threading.Lock()
        self.last_call = 0.0

    def wait(self):
        if self.max_rps <= 0:
            return  # No speed limit
        
        min_interval = 1.0 / self.max_rps
        with self.lock:
            now = time.perf_counter()
            elapsed = now - self.last_call
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self.last_call = time.perf_counter()


@dataclass
class TestResult:
    name: str
    method: str
    url: str
    attempts: int
    ok: bool
    duration_ms: int
    expected_status: int
    actual_status: Optional[int]
    max_response_time_ms: int
    errors: List[str]
    debug_output: Optional[str]

    @property
    def endpoint_path(self) -> str:
        try:
            return "/" + self.url.split("://", 1)[1].split("/", 1)[1]
        except Exception:
            return self.url


def load_config(path: str) -> Tuple[Optional[str], float, List[Dict[str, Any]]]:
    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    if isinstance(config, list):
        return None, DEFAULT_MAX_RPS, config
    
    if isinstance(config, dict) and isinstance(config.get("tests"), list):
        base_url = config.get("base_url")
        max_rps = float(config.get("max_rps", DEFAULT_MAX_RPS))
        return base_url, max_rps, config["tests"]
        
    raise ValueError('config.json must be either a JSON array, or an object with key "tests"')


def _as_int_ms(seconds: float) -> int:
    return int(round(seconds * 1000))


def resolve_url(base_url: Optional[str], url: str) -> str:
    u = url.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    if u.startswith("/") and base_url:
        return base_url.rstrip("/") + u
    return u


def _debug_snippet(response: Optional[requests.Response], request_error: Optional[str]) -> Optional[str]:
    if request_error and response is None:
        return request_error[:DEBUG_SNIPPET_CHARS]
    if response is None:
        return None
    try:
        ctype = (response.headers.get("Content-Type") or "").lower()
        if "application/json" in ctype:
            try:
                return json.dumps(response.json(), ensure_ascii=False)[:DEBUG_SNIPPET_CHARS]
            except Exception:
                pass
        txt = (response.text or "").strip().replace("\r\n", "\n")
        return txt[:DEBUG_SNIPPET_CHARS] if txt else "(empty body)"
    except Exception as e:
        return f"(debug_snippet_error: {type(e).__name__}: {e})"[:DEBUG_SNIPPET_CHARS]


def validate_response(
    response: Optional[requests.Response],
    test: Dict[str, Any],
    duration_ms: int,
    request_error: Optional[str],
) -> Tuple[bool, Optional[int], List[str]]:
    errors: List[str] = []

    expected_status = int(test["expected_status"])
    max_ms = int(test["max_response_time_ms"])
    expected_keys = test.get("expected_keys", [])

    if request_error:
        errors.append(request_error)
        return False, None, errors

    assert response is not None
    actual_status = response.status_code

    if actual_status != expected_status:
        errors.append(f"Status code mismatch (Expected {expected_status}, Got {actual_status})")

    if duration_ms > max_ms:
        errors.append(f"Response time exceeded (Took {duration_ms}ms, Max {max_ms}ms)")

    if expected_keys:
        try:
            data = response.json()
            if not isinstance(data, dict):
                errors.append("Response JSON is not an object/dict")
            else:
                for key in expected_keys:
                    if key not in data:
                        errors.append(f"Missing key '{key}' in response JSON")
        except ValueError:
            errors.append("Response is not valid JSON")

    return len(errors) == 0, actual_status, errors


def run_test(test: Dict[str, Any], base_url: Optional[str], rate_limiter: RateLimiter) -> TestResult:
    name = str(test.get("name", "Unnamed Test"))
    method = str(test["method"]).upper()
    url = resolve_url(base_url, str(test["url"]))
    expected_status = int(test["expected_status"])
    max_response_time_ms = int(test["max_response_time_ms"])

    timeout_seconds = float(test.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    retries = int(test.get("retries", DEFAULT_RETRIES))
    headers = test.get("headers")
    payload_json = test.get("json")
    payload_data = test.get("data")

    start = time.perf_counter()
    response: Optional[requests.Response] = None
    request_error: Optional[str] = None
    attempts = 0

    for _ in range(max(1, 1 + retries)):
        attempts += 1
        response = None
        request_error = None
        
        # ---> THE BOUNCER <---
        # Before sending the request, the thread checks if it's going too fast
        rate_limiter.wait()
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                json=payload_json,
                data=payload_data,
                timeout=timeout_seconds,
            )
            if response.status_code >= 500 and attempts <= retries:
                continue
            break
        except (requests.Timeout, requests.ConnectionError):
            request_error = f"Network timeout/connection error (attempt {attempts}/{1 + retries})"
            if attempts <= retries:
                continue
        except requests.RequestException as e:
            request_error = f"Network error: {type(e).__name__}: {e}"
        break

    duration_ms = _as_int_ms(time.perf_counter() - start)

    ok, actual_status, errors = validate_response(response, test, duration_ms, request_error)
    debug_output = None if ok else _debug_snippet(response, request_error)
    return TestResult(
        name=name,
        method=method,
        url=url,
        attempts=attempts,
        ok=ok,
        duration_ms=duration_ms,
        expected_status=expected_status,
        actual_status=actual_status,
        max_response_time_ms=max_response_time_ms,
        errors=errors,
        debug_output=debug_output,
    )


def write_report(results: List[TestResult], wall_time_s: float, report_path: str) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.ok)
    failed = total - passed

    sequential_ms_estimate = sum(r.duration_ms for r in results)
    concurrent_ms = _as_int_ms(wall_time_s)
    speedup = (sequential_ms_estimate / concurrent_ms) if concurrent_ms > 0 else 0.0

    failed_results = [r for r in results if not r.ok]
    results_sorted = sorted(results, key=lambda r: (r.ok, r.duration_ms), reverse=False)

    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: List[str] = []
    lines.append("# API Test Summary")
    lines.append("")
    lines.append(f"_Generated: {now}_")
    lines.append("")
    lines.append(f"**Total Tests:** {total}  ")
    lines.append(f"**Passed:** {passed}  ")
    lines.append(f"**Failed:** {failed}  ")
    lines.append("")
    lines.append("## Timing")
    lines.append(f"- **Concurrent wall time:** {concurrent_ms}ms")
    lines.append(f"- **Estimated sequential time:** {sequential_ms_estimate}ms")
    lines.append(f"- **Estimated speedup:** {speedup:.2f}x")
    lines.append("")
    lines.append("## Results (slowest/failing first)")
    for r in results_sorted:
        status = "PASS" if r.ok else "FAIL"
        lines.append(f"- **[{status}]** `{r.method}` `{r.endpoint_path}` — {r.duration_ms}ms (max {r.max_response_time_ms}ms)")
    lines.append("")

    if failed_results:
        lines.append("## Failed Endpoints")
        for r in failed_results:
            lines.append(f"### {r.name}")
            lines.append(f"- **Request:** `{r.method}` `{r.url}`")
            lines.append(f"- **Attempts:** {r.attempts}")
            lines.append(f"- **Expected Status:** {r.expected_status}")
            lines.append(f"- **Actual Status:** {r.actual_status if r.actual_status is not None else 'N/A'}")
            lines.append(f"- **Response Time:** {r.duration_ms}ms (max {r.max_response_time_ms}ms)")
            for err in r.errors:
                lines.append(f"- **Error:** {err}")
            if r.debug_output:
                lines.append("- **Debug Output:**")
                lines.append("")
                lines.append("```")
                lines.append(r.debug_output)
                lines.append("```")
            lines.append("")
    else:
        lines.append("## Failed Endpoints")
        lines.append("_None_")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")


def main() -> int:
    print("Starting concurrent API test engine...")

    try:
        config_base_url, config_max_rps, tests = load_config(CONFIG_PATH)
    except Exception as e:
        print(f"[ERROR] Failed to load {CONFIG_PATH}: {e}")
        return 2

    base_url = os.environ.get("API_TEST_BASE_URL") or config_base_url
    
    # Set up Rate Limiter (Environment variable overrides config.json)
    env_rps = os.environ.get("API_TEST_MAX_RPS")
    max_rps = float(env_rps) if env_rps is not None else config_max_rps
    rate_limiter = RateLimiter(max_rps)

    max_workers = int(os.environ.get("API_TEST_WORKERS", DEFAULT_MAX_WORKERS))
    max_workers = max(1, min(max_workers, 32))

    if max_rps > 0:
        print(f"Testing {len(tests)} endpoints using {min(max_workers, len(tests))} threads (Rate Limit: {max_rps} req/sec)...\n")
    else:
        print(f"Testing {len(tests)} endpoints using {min(max_workers, len(tests))} threads (No Rate Limit)...\n")

    started = time.perf_counter()
    results: List[TestResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run_test, t, base_url, rate_limiter) for t in tests]
        for future in concurrent.futures.as_completed(futures):
            r = future.result()
            results.append(r)
            if r.ok:
                print(f"[PASS] {r.method} {r.endpoint_path} ({r.duration_ms}ms)")
            else:
                reason = r.errors[0] if r.errors else "Unknown failure"
                print(f"[FAIL] {r.method} {r.endpoint_path} - {reason}")

    wall = time.perf_counter() - started
    write_report(results, wall, REPORT_PATH)

    print(f"\nTest suite complete in {_as_int_ms(wall)} ms.")
    print(f"Markdown report written to: {REPORT_PATH}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())