# Enterprise API Tester: Architecture Flow

This diagram outlines the multithreaded execution flow of the testing engine. 

```mermaid
graph TD
    A[Start: main.py] --> B[Load config.json]
    B --> C[Initialize RateLimiter & ThreadPoolExecutor]
    C --> D[Deploy Worker Threads]
    
    subgraph Concurrent Execution
        D --> E[Thread Picks Up Test Case]
        E --> F{RateLimiter Bouncer}
        F -->|Wait if too fast| F
        F -->|Approved| G[Send HTTP Request]
        
        G -->|Timeout / 5xx Error| H{Retry Check}
        H -->|Under Limit| F
        H -->|Over Limit| I[Mark as Failed]
        
        G -->|Success or 4xx| J[Validate Status Code & JSON Keys]
    end
    
    I --> K[Collect TestResult Object]
    J --> K
    
    K --> L[All Threads Complete]
    L --> M[Generate test-report.md]
    M --> N[End Process]