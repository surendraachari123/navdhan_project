# Flow Diagrams - NavDhan Mutual Fund API

## System Architecture Overview

```mermaid
graph TB
    subgraph "Client Layer"
        Browser[Web Browser]
        CLI[Python CLI Client]
    end
    
    subgraph "Frontend - Static Files"
        HTML[HTML Pages<br/>login, dashboard, ai, admin...]
        JS[app.js, config.js]
        CSS[style.css]
    end
    
    subgraph "Backend - FastAPI Server"
        Main[main.py<br/>App Entry & Routes]
        Auth[auth.py<br/>JWT & RBAC]
        Models[models.py<br/>Pydantic Validation]
        DB[database.py<br/>Connection Pool]
        AI_Router[ai_router.py]
        AI_Service[ai_service.py]
        AI_LLM[ai_llm.py]
        AI_Qdrant[ai_qdrant.py]
        AI_Guard[ai_sql_guard.py]
        ETL[etl_runner.py<br/>horse_pipeline.py]
        Email[email_service.py]
        Exceptions[exceptions.py]
    end
    
    subgraph "External Services"
        Oracle[(Oracle Database)]
        Qdrant[(Qdrant Vector DB)]
        Groq[Groq API<br/>LLM]
        Gmail[SMTP Email]
        S3[AWS S3]
    end
    
    Browser --> HTML
    HTML --> JS
    JS -->|REST API Calls| Main
    CLI -->|REST API Calls| Main
    
    Main --> Auth
    Main --> Models
    Main --> AI_Router
    Main --> ETL
    Main --> Email
    Main --> Exceptions
    
    AI_Router --> AI_Service
    AI_Service --> AI_LLM
    AI_Service --> AI_Qdrant
    AI_Service --> AI_Guard
    AI_Service --> DB
    
    Auth --> DB
    ETL --> DB
    ETL --> S3
    Email --> Gmail
    
    DB --> Oracle
    AI_Qdrant --> Qdrant
    AI_LLM --> Groq
```

## 1. User Authentication Flow

```mermaid
sequenceDiagram
    actor User
    participant Browser
    participant Frontend as app.js
    participant API as FastAPI /login
    participant Auth as auth.py
    participant DB as Oracle
    
    User->>Browser: Navigate to login.html
    User->>Frontend: Enter username & password
    Frontend->>API: POST /login<br/>Form: username, password
    API->>Auth: authenticate_user(username, password, cursor)
    Auth->>DB: SELECT username, password_hash, is_active, role, force_password_change<br/>FROM app_users WHERE username=?
    
    alt User not found or password wrong
        DB-->>Auth: No match / wrong
        Auth-->>API: Raise AuthError(401)
        API-->>Frontend: 401 "Incorrect username or password"
        Frontend-->>User: Show error
    else Account disabled
        DB-->>Auth: is_active = 0
        Auth-->>API: Raise AuthError(401)
        API-->>Frontend: 401 "This account is disabled"
        Frontend-->>User: Show error
    else Valid credentials
        Auth->>Auth: verify_password() with bcrypt
        Auth->>DB: UPDATE app_users SET last_login = SYSDATE
        Auth->>Auth: create_access_token(username, role)
        Auth-->>API: Return {access_token, token_type, role, force_password_change}
        API-->>Frontend: 200 OK with token
        Frontend->>Frontend: localStorage.setItem('token', access_token)<br/>localStorage.setItem('role', role)
        
        alt force_password_change = true
            Frontend-->>User: Redirect to change-password.html
        else
            Frontend-->>User: Redirect to dashboard.html
        end
    end
```

## 2. Registration & Admin Approval Flow

```mermaid
sequenceDiagram
    actor User
    participant Browser
    participant Frontend
    participant API as FastAPI
    participant DB as Oracle
    participant Email as email_service
    
    Note over User,Email: User Registration Request
    User->>Frontend: Fill register.html form
    Frontend->>API: POST /register<br/>{username, full_name, email, mobile, requested_role, reason}
    API->>DB: Check if username exists in app_users
    API->>DB: Check if pending request exists
    alt Duplicate
        API-->>Frontend: 409 DuplicateError
        Frontend-->>User: Show error
    else Valid
        API->>DB: INSERT INTO registration_requests (PENDING)
        API-->>Frontend: 201 "Registration request submitted"
        Frontend-->>User: Show success message
    end
    
    Note over User,Email: Admin Approval
    actor Admin
    Admin->>Frontend: Open admin-requests.html
    Frontend->>API: GET /admin/registration-requests?status=PENDING<br/>Header: Bearer token
    API->>API: require_role("admin")
    API->>DB: SELECT * FROM registration_requests WHERE status='PENDING'
    API-->>Frontend: List of requests
    Frontend-->>Admin: Display requests
    
    Admin->>Frontend: Click Approve
    Frontend->>API: POST /admin/registration-requests/{id}/approve<br/>{role?}
    API->>DB: SELECT request details
    API->>DB: Check status is PENDING
    API->>Auth: generate_temp_password() & hash_password()
    API->>DB: INSERT INTO app_users (username, password_hash, email, role, force_password_change=1)
    API->>DB: UPDATE registration_requests SET status='APPROVED', reviewed_at=SYSDATE, reviewed_by=admin
    API->>Email: send_new_account_email(email, username, temp_password)
    Email-->>User: Email with temp credentials
    API-->>Frontend: 200 Success
    Frontend-->>Admin: Show success
    
    Note over User,Email: First Login with Temp Password
    User->>Frontend: Login with temp credentials
    Frontend->>API: POST /login
    API-->>Frontend: {token, force_password_change: true}
    Frontend-->>User: Redirect to change-password.html
    User->>Frontend: Change password
    Frontend->>API: POST /change-password<br/>{current_password, new_password}
    API->>DB: Verify current password
    API->>DB: UPDATE app_users SET password_hash=new_hash, force_password_change=0
    API-->>Frontend: 200 Success
    Frontend-->>User: Redirect to dashboard.html
```

## 3. Investor & Transaction Management Flow

```mermaid
sequenceDiagram
    actor User
    participant Browser
    participant Frontend
    participant API as FastAPI
    participant DB as Oracle
    
    Note over User,DB: Create Investor
    User->>Frontend: Fill investor form (dashboard.html)
    Frontend->>API: POST /investors<br/>{investor_id, name, email, mobile, kyc_status, pan}
    API->>API: Validate with Pydantic (PAN format, email, mobile, kyc_status)
    API->>DB: Check if investor_id exists
    alt Exists
        API-->>Frontend: 409 DuplicateError
    else New
        API->>DB: INSERT INTO investor_table
        API->>DB: COMMIT
        API-->>Frontend: 201 Created
        Frontend-->>User: Refresh list
    end
    
    Note over User,DB: Create Transaction
    User->>Frontend: Add transaction for investor
    Frontend->>API: POST /transactions<br/>{folio_id, investor_id, scheme_id, units, nav, amount, txn_type, txn_date}
    API->>API: Validate (amounts > 0, txn_type in PURCHASE/SIP/REDEMPTION)
    API->>DB: SELECT 1 FROM investor_table WHERE investor_id=?
    alt Investor not found
        API-->>Frontend: 404 NotFoundError
    else Exists
        API->>DB: INSERT INTO mfund_transactions
        API->>DB: COMMIT
        API-->>Frontend: 201 Created
    end
    
    Note over User,DB: View Transactions
    User->>Frontend: View investor transactions
    Frontend->>API: GET /investors/{investor_id}/transactions
    API->>DB: SELECT investor details
    API->>DB: SELECT transactions for investor
    API-->>Frontend: {investor details + transactions array}
    Frontend-->>User: Display in table
```

## 4. NavDhan AI - Natural Language to SQL Flow

```mermaid
sequenceDiagram
    actor User
    participant Browser
    participant Frontend as ai.html
    participant API as /ai/ask or /ai/chat
    participant Service as ai_service
    participant Qdrant as ai_qdrant
    participant LLM as Groq API
    participant Guard as ai_sql_guard
    participant DB as Oracle
    
    User->>Frontend: Type question in natural language
    Frontend->>API: POST /ai/ask {question} or POST /ai/chat {messages}
    API->>Service: ask() or converse()
    Service->>Service: Get user role, determine blocked columns
    
    Note over Service,Qdrant: Context Retrieval
    Service->>Qdrant: search(question, limit=6, restricted=role!=admin)
    Qdrant->>Qdrant: Embed question with fastembed
    Qdrant->>Qdrant: Vector similarity search in navdhan_fastapi_schema
    Qdrant-->>Service: Relevant glossary docs (rules, examples, table info)
    Service->>Qdrant: get_global_rules(restricted)
    Qdrant-->>Service: Global SQL rules
    
    Service->>DB: Load schema from USER_TAB_COLUMNS (cached 10 min)
    DB-->>Service: Column definitions for allowed tables
    Service->>Service: Build schema text (hide sensitive columns for non-admin)
    Service->>Service: Build system prompt with schema + context
    
    Note over Service,LLM: SQL Generation
    Service->>LLM: chat_json(messages with system prompt + history)
    LLM-->>Service: {action: "sql", sql: "..."} or {action: "answer", reply: "..."}
    
    alt action = "answer"
        Service-->>API: Return {mode: "answer", answer: "..."}
        API-->>Frontend: Display answer
    else action = "sql"
        Service->>Guard: validate_sql(sql, allowed_tables, blocked_columns)
        Guard->>Guard: Strip fences/semicolons
        Guard->>Guard: Check forbidden keywords (INSERT/UPDATE/DELETE/DDL)
        Guard->>Guard: Parse with sqlglot (Oracle dialect)
        Guard->>Guard: Check single SELECT statement
        Guard->>Guard: Validate tables against allowlist
        Guard->>Guard: Check blocked columns (PAN/mobile/email for non-admin)
        Guard->>Guard: Block SELECT * if sensitive columns exist for non-admin
        
        alt Validation fails
            Guard-->>Service: Raise UnsafeSQLError
            alt First attempt
                Service->>Service: Add error to messages, retry (SQL_ATTEMPTS=2)
                Service->>LLM: chat_json with correction prompt
                Note over Service,LLM: Retry loop...
            else Second attempt failed
                Service-->>API: Raise UnsafeSQLError(400)
                API-->>Frontend: 400 with error message
            end
        else Validation passes
            Service->>Guard: wrap_with_row_limit(sql, max_rows=200)
            Guard-->>Service: Wrapped SQL with ROWNUM <= 201
            Service->>DB: SET TRANSACTION READ ONLY
            Service->>DB: SET call_timeout = 15000ms
            Service->>DB: Execute wrapped SQL
            DB-->>Service: Results rows
            
            Service->>Service: Convert to JSON-serializable format
            Service->>Service: Check if truncated (>200 rows)
            Service->>LLM: Summarize results in plain English
            LLM-->>Service: Natural language summary
            Service-->>API: Return {mode: "sql", answer, sql, columns, rows, row_count, truncated, context}
            API-->>Frontend: Display answer + table + "View SQL" + sources
            Frontend-->>User: Show results
        end
    end
```

## 5. AI Insights Flow

```mermaid
sequenceDiagram
    actor User as Admin/Manager
    participant Browser
    participant Frontend as ai.html (Insights tab)
    participant API as /ai/insights
    participant Service as ai_service
    participant DB as Oracle
    participant LLM as Groq API
    
    User->>Frontend: Click "Insights" tab
    Frontend->>API: GET /ai/insights<br/>Header: Bearer token
    API->>API: require_role("admin", "manager")
    API->>Service: get_insights(cursor, force_refresh=false)
    
    Service->>Service: Check cache<br/>(TTL = AI_INSIGHTS_CACHE_MINUTES = 10 min)
    
    alt Cached and not expired
        Service-->>API: Return cached data with cached=true
        API-->>Frontend: Display cached insights
    else Need to compute
        Service->>Service: _compute_metrics()
        Note over Service,DB: Run pre-defined queries
        Service->>DB: SET TRANSACTION READ ONLY
        Service->>DB: Overview query (counts, inflows, outflows)
        Service->>DB: KYC status distribution
        Service->>DB: Transaction type mix
        Service->>DB: Monthly trend (last 12 months)
        Service->>DB: Top schemes by net investment
        Service->>DB: Top investors by net investment
        Service->>DB: KYC risk (transacting without verified KYC)
        Service->>DB: Dormant investors (no transactions)
        DB-->>Service: All metrics
        
        Service->>Service: Calculate net_investment
        Service->>Service: Calculate month-over-month changes
        
        Note over Service,LLM: Generate narrative
        alt GROQ_API_KEY configured
            Service->>LLM: chat_json with metrics JSON
            LLM-->>Service: {headline, insights[], suggested_questions[]}
            Service->>Service: Cache result (only if narrative generated)
            Service-->>API: Return full data with cached=false
        else Not configured
            Service-->>API: Return metrics with narrative=null, narrative_error
        end
        
        API-->>Frontend: Display KPIs + charts + narrative + suggested questions
    end
    
    User->>Frontend: Click "Refresh"
    Frontend->>API: GET /ai/insights?refresh=true
    API->>Service: force_refresh=true
    Note over Service: Bypass cache, recompute
```

## 6. ETL Pipeline Flow

```mermaid
sequenceDiagram
    actor Admin
    participant Browser
    participant Frontend as dashboard.html/admin
    participant API as /run-etl or /run-horse-etl
    participant DB as Oracle
    participant BG as BackgroundTasks
    participant ETL as etl_runner.py / horse_pipeline.py
    participant S3 as AWS S3
    
    Admin->>Frontend: Click "Run ETL" or "Run Horse ETL"
    Frontend->>API: POST /run-etl (or /run-horse-etl)<br/>Header: Bearer token (admin)
    API->>API: require_role("admin")
    API->>DB: INSERT INTO etl_jobs (job_id, triggered_by, status='RUNNING')
    API->>BG: add_task(run_etl_job, job_id) or run_horse_pipeline_job
    API-->>Frontend: {job_id, status: "RUNNING"}
    Frontend-->>Admin: Show "Job started" with job_id
    
    Note over BG,ETL: Background Execution
    BG->>ETL: Execute job
    ETL->>DB: Update job status/progress (implicit via logging)
    
    alt Standard ETL
        ETL->>DB: Run SQL scripts from etl_jobs.sql
        ETL->>ETL: Process data
    else Horse ETL
        ETL->>S3: Download Excel file (S3_HORSE_DATA_KEY)
        S3-->>ETL: Excel data
        ETL->>ETL: Parse with pandas
        ETL->>DB: Insert/Update Owner, Breeder, Dam, Horse, Alias tables
    end
    
    alt Success
        ETL->>DB: UPDATE etl_jobs SET status='COMPLETED', output='...', finished_at=SYSDATE
    else Error
        ETL->>DB: UPDATE etl_jobs SET status='FAILED', error_message='...', finished_at=SYSDATE
    end
    
    Note over Admin,DB: Check Status
    Admin->>Frontend: Poll or click "Check Status"
    Frontend->>API: GET /run-etl/{job_id}
    API->>DB: SELECT * FROM etl_jobs WHERE job_id=?
    DB-->>API: Job details
    API-->>Frontend: {status, output, error_message, started_at, finished_at}
    Frontend-->>Admin: Display status
```

## 7. Database Connection Pool Flow

```mermaid
sequenceDiagram
    participant App as FastAPI App
    participant Lifespan as lifespan()
    participant Pool as database.py
    participant Oracle as Oracle DB
    participant Route as Route Handler
    participant Dep as get_db() dependency
    
    Note over App,Oracle: Startup
    App->>Lifespan: Call lifespan() on startup
    Lifespan->>Pool: init_pool()
    Pool->>Oracle: oracledb.create_pool(user, password, dsn, min, max, increment)
    Oracle-->>Pool: Connection pool created
    Pool-->>Lifespan: Pool ready
    Lifespan-->>App: App ready to serve
    
    Note over Route,Oracle: Request Handling
    Route->>Dep: Depends(get_db)
    Dep->>Pool: get_connection()
    Pool->>Oracle: pool.acquire()
    Oracle-->>Pool: Borrow connection
    Pool->>Pool: get_connection() yields connection
    Dep->>Dep: Create cursor from connection
    Dep->>Route: yield cursor
    Route->>Oracle: Execute SQL via cursor
    Oracle-->>Route: Results
    
    alt Success
        Route-->>Dep: Return normally
        Dep->>Oracle: connection.commit()
    else Exception
        Route-->>Dep: Raise exception
        Dep->>Oracle: connection.rollback()
        Dep-->>App: Re-raise exception
        App->>App: Exception handler processes
    end
    
    Dep->>Pool: cursor.close()
    Dep->>Pool: Return connection to pool (finally block)
    Pool->>Oracle: pool.release(connection)
    
    Note over App,Oracle: Shutdown
    App->>Lifespan: App shutting down
    Lifespan->>Pool: close_pool()
    Pool->>Oracle: pool.close(force=True)
    Oracle-->>Pool: All connections closed
```

## 8. Error Handling Flow

```mermaid
graph TD
    Request[Incoming Request] --> Route[Route Handler]
    Route --> Try{Try to Execute}
    
    Try -->|Validation Error| VE[RequestValidationError<br/>Pydantic]
    Try -->|App Error| AE[AppError subclasses<br/>NotFound, Duplicate, etc.]
    Try -->|Integrity Error| IE[oracledb.IntegrityError]
    Try -->|DB Error| DE[oracledb.DatabaseError]
    Try -->|Other Error| OE[Generic Exception]
    Try -->|Success| Success[Return Response]
    
    VE --> VEH[validation_error_handler<br/>422]
    AE --> AEH[app_error_handler<br/>Custom status code]
    IE --> IEH[integrity_error_handler<br/>409]
    DE --> DEH{Check error code}
    DEH -->|Connection error codes<br/>12154,12541,3113,3114,1034,12170| CE[503 DatabaseUnavailableError]
    DEH -->|Other DB error| DBE[500 DatabaseError<br/>Generic message]
    OE --> OEH[unhandled_exception_handler<br/>500 InternalServerError<br/>Log full traceback]
    
    VEH --> Resp[JSON Response<br/>{error: {message, type}}]
    AEH --> Resp
    IEH --> Resp
    CE --> Resp
    DBE --> Resp
    OEH --> Resp
```

## Component Interaction Summary

| Component | Interacts With | Purpose |
|-----------|----------------|---------|
| **Frontend (HTML/JS)** | FastAPI REST API | User interface, token management |
| **main.py** | All modules, FastAPI framework | App orchestration, routing, middleware, error handling |
| **auth.py** | database.py, config.py, exceptions.py | Authentication, authorization, token management |
| **database.py** | Oracle DB via oracledb | Connection pooling, transaction management |
| **ai_service.py** | ai_llm, ai_qdrant, ai_sql_guard, database | AI orchestration and business logic |
| **ai_llm.py** | Groq API (HTTP) | LLM communication |
| **ai_qdrant.py** | Qdrant DB (HTTP/gRPC) | Vector search and glossary storage |
| **ai_sql_guard.py** | sqlglot | SQL validation and security |
| **etl_runner.py** | database.py, pandas | Data processing pipelines |
| **horse_pipeline.py** | S3 (boto3), database.py, pandas, openpyxl | Horse data ingestion from S3 |
| **email_service.py** | SMTP server | Email notifications |

---

**Note**: These diagrams use Mermaid syntax and can be rendered in GitHub, GitLab, or most Markdown viewers that support Mermaid.
