# 🚀 Enterprise API Testing Engine

A lightweight, concurrent, and rate-limited API testing framework built in pure Python. 

Designed to simulate real-world API load testing, this engine validates endpoints in parallel while enforcing strict rate limits to ensure safe, stable execution without overwhelming target servers.

## 🌟 Key Features
- **Concurrent Execution:** Utilizes `ThreadPoolExecutor` to run test cases in parallel, dramatically reducing overall test suite runtime.
- **Thread-Safe Rate Limiting:** Implements a global lock to enforce strict Requests Per Second (RPS) limits, acting as a "polite citizen" on the network.
- **Smart Retries & Fault Tolerance:** Gracefully handles transient network timeouts and `5xx` server errors with configurable retry logic.
- **Deep Validation:** Verifies HTTP status codes, maximum allowed response times (ms), and required JSON keys in the payload.
- **Automated Debug Reporting:** Automatically generates a comprehensive `test-report.md` file, capturing raw JSON payloads for any failed requests to accelerate debugging.
- **Containerized:** Fully packaged with Docker for "run anywhere" consistency.

## 🏗 Architecture
View the exact flow of control and multithreaded architecture in the [FLOW.md](FLOW.md) diagram.

## ⚙️ Configuration (`config.json`)
The engine is driven by a simple JSON configuration file. It supports `GET`, `POST`, `PUT`, and `DELETE` requests, alongside custom headers and payloads.

```json
{
  "base_url": "[https://jsonplaceholder.typicode.com](https://jsonplaceholder.typicode.com)",
  "max_rps": 5,
  "tests": [
    {
      "name": "User Fetch Test",
      "method": "GET",
      "url": "/users/1",
      "expected_status": 200,
      "max_response_time_ms": 1500,
      "expected_keys": ["id", "name"]
    }
  ]
}


