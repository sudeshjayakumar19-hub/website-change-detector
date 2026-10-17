# Website Change Detector — Vinnovate IT Recruitment Assessment

A complete, production-minded full-stack web application that monitors public HTTP/HTTPS webpages, extracts meaningful readable content, filters out dynamic noise, stores deduplicated version snapshots, renders side-by-side and unified diffs, and dispatches keyword-aware change notifications.

---

## 1. Project Overview

**Website Change Detector** enables users to track updates across public websites without false positives from dynamic ads, timestamps, scripts, or layout counters. Users can configure custom check intervals (15 minutes to 24 hours), scope monitoring to specific CSS selectors, ignore noisy page regions, watch for specific keywords, inspect chronological version histories, compare any two snapshots side-by-side, subscribe to page-specific RSS feeds, and receive alerts via in-app notifications, SMTP email, or webhooks (Discord / Telegram / Slack / custom JSON).

---

## 2. Features Implemented

### Core Features
- **Responsive Dashboard (Dark Navy & Warm Orange Theme)**:
  - Live KPI cards for **Total Monitored Websites**, **Websites With Detected Changes**, **Successful Checks**, and **Failed Checks**.
  - Interactive status filtering (`All`, `Changed`, `Unchanged`, `Failed / Error`, `Pending`) and real-time search by name, URL, selector, or keyword.
  - One-click **Check Now**, **History & Diff**, **RSS Feed**, and **Delete** (with confirmation modal).
  - Toast notifications, loading spinners, empty states, and structured error banners.
- **Website Registration & Validation**:
  - Frontend and backend validation for public `http://` and `https://` URLs.
  - Enforces check intervals between **15 minutes** and **24 hours (1440 minutes)**.
  - Optional target CSS selector, ignore CSS selector, and comma-separated keywords (validated via `soupsieve` and Pydantic).
  - Never prompts for or stores website credentials.
- **Intelligent Monitoring & Content Extraction Engine**:
  - Fetches pages with a configurable timeout, descriptive `User-Agent`, and response byte limit (`MAX_RESPONSE_BYTES`).
  - Manually follows redirects up to `MAX_REDIRECTS` while re-validating every redirect target against SSRF rules.
  - Strips scripts, styles, comments, SVGs, iframes, navigation/footer noise, ads, cookie banners, relative timestamps (`"updated 2 minutes ago"`), ISO timestamps, and view counters (`"1,024 views"`).
  - Normalizes whitespace and computes a deterministic **SHA-256** digest of meaningful content.
  - Captures initial baseline snapshot (`v1`) without triggering false alerts, and only creates new snapshots when the SHA-256 content hash changes.
  - Respects `robots.txt` directives, enforces per-host rate limits, prevents concurrent duplicate checks on the same site/URL, and refuses to bypass CAPTCHAs or HTTP 401/403 authentication walls.
- **Version History & Diff Viewer**:
  - Chronological snapshot list with version numbers, timestamps, word/character counts, matched keywords, and SHA-256 hashes.
  - **Side-by-Side Diff** and **Unified Diff** views with added content highlighted in green and removed content highlighted in red.
  - Clear banner when comparing two versions that have no meaningful differences.
- **Alerts & Notification System**:
  - Always-on **In-App notifications** (zero external credentials required).
  - Optional **SMTP Email** and **Generic Webhook** notifications (compatible with Discord, Telegram, Slack, and custom JSON endpoints with optional HMAC-SHA256 signing).
  - Deterministic **duplicate-alert prevention** (`channel:site_id:content_hash`).
- **Scheduler & Reliability**:
  - Background `APScheduler` (`AsyncIOScheduler`) running non-overlapping interval jobs (`max_instances=1`, `coalesce=True`) with bounded retries and exponential backoff.

### Bonus Features Implemented
1. **7-Day Dashboard Activity Chart**: Visualizes daily detected changes, successful checks, and failed checks over the past 7 days.
2. **Keyword-Only Alerts Mode**: Per-site toggle (`keyword_only_alerts`) to suppress notifications unless a detected change matches at least one configured keyword.
3. **Deterministic One-Line Change Summary**: Local diff summarization algorithm (`generate_change_summary`) that produces concise human-readable descriptions (e.g., `Updated "Price: $29 per month" → "Price: $39 per month"`).
4. **Page-Specific RSS 2.0 Feed**: `GET /api/sites/{id}/rss` serves a valid XML RSS feed of detected changes for any monitored website.
5. **Visual Before-and-After Comparison**: Full-text side-by-side normalized reader tab alongside the line-by-line diff views.

---

## 3. Architecture & Data Flow

```mermaid
flowchart LR
    UI["Responsive Frontend\n(HTML5 / CSS3 / Vanilla JS)"] <--> API["FastAPI REST API\n(app/api/routes.py)"]
    SCHED["APScheduler\n(app/services/scheduler.py)"] --> MON["Monitoring Engine\n(app/services/monitor.py)"]
    API --> MON
    MON --> SEC["SSRF & Robots Guard\n(app/services/security.py)"]
    MON --> EXT["BeautifulSoup Extractor\n(app/services/extractor.py)"]
    MON --> DIFF["Diff & Summary Engine\n(app/services/differ.py)"]
    MON --> NOTIF["Notification Dispatcher\n(app/services/notifier.py)"]
    MON --> DB[("SQLite + SQLAlchemy\n(change_detector.db)")]
    NOTIF --> DB
```

### Project Structure

```text
web -change -detector/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app initialization, lifespan & HTML routes
│   ├── config.py            # Environment settings via pydantic-settings
│   ├── database.py          # SQLAlchemy engine, session factory & SQLite FK pragma
│   ├── models.py            # ORM models: MonitoredSite, PageSnapshot, CheckRun, Notification
│   ├── schemas.py           # Pydantic request/response schemas & validators
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py        # REST API endpoints (/api/*)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── security.py      # URL normalization, DNS validation & SSRF protection
│   │   ├── extractor.py     # BeautifulSoup extraction, noise removal & SHA-256 hashing
│   │   ├── differ.py        # Unified diff, side-by-side diff & one-line change summary
│   │   ├── monitor.py       # Webpage fetching, rate limiting, retries & check orchestration
│   │   ├── notifier.py      # In-App, SMTP Email & Webhook alert delivery with deduplication
│   │   ├── scheduler.py     # APScheduler periodic background jobs
│   │   └── rss.py           # RSS 2.0 XML feed builder
│   ├── templates/
│   │   ├── index.html       # Main dashboard view
│   │   └── history.html     # Site version history & diff comparison view
│   └── static/
│       ├── css/
│       │   └── styles.css   # Dark navy & warm orange responsive stylesheet
│       └── js/
│           ├── dashboard.js # Dashboard state, chart, modals & API integration
│           └── history.js   # Diff viewer & version history controller
├── tests/
│   ├── __init__.py
│   └── test_app.py          # Automated pytest suite (32 test cases)
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 4. Technology Stack

- **Backend**: Python 3.11+ (tested on Python 3.13), FastAPI, Uvicorn
- **Database**: SQLite with SQLAlchemy 2.0 ORM (`PRAGMA foreign_keys=ON` for cascade cleanup)
- **HTML Parsing & Extraction**: BeautifulSoup4 (`lxml` parser), `soupsieve`
- **HTTP Client & Optional JS Rendering**: `httpx` (async), `playwright` (optional headless browser fallback)
- **Background Scheduling**: `APScheduler` (`AsyncIOScheduler`)
- **Frontend**: Semantic HTML5, modern CSS3, Vanilla JavaScript (zero external JS framework dependencies)
- **Testing**: `pytest`, `httpx.MockTransport`, FastAPI `TestClient`

---

## 5. Installation & Setup (Windows PowerShell)

### Step 1: Clone or Open the Project Directory

```powershell
cd "c:\Users\Sudeshna.J\OneDrive\Desktop\web -change -detector"
```

### Step 2: Create and Activate a Virtual Environment

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

*(If `py -3.13` is not used, `python -m venv .venv` with Python 3.11+ also works.)*

### Step 3: Install Dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

*(Optional)* If you want to use the headless Playwright browser fallback for JavaScript-heavy single-page applications:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

### Step 4: Configure Environment Variables (Optional)

The application works out-of-the-box with sensible defaults and requires **no external API keys or credentials**. To customize settings, copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

---

## 6. Running the Application

Start the FastAPI server with Uvicorn from PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open your browser to:
- **Dashboard UI**: `http://127.0.0.1:8000/`
- **Swagger UI API Documentation**: `http://127.0.0.1:8000/docs`
- **ReDoc API Documentation**: `http://127.0.0.1:8000/redoc`
- **Health Check Endpoint**: `http://127.0.0.1:8000/api/health`

---

## 7. Running Automated Tests

Run the full `pytest` suite in Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

### Verified Test Results

All **32 automated tests** pass using isolated in-memory SQLite databases and mocked HTTP transports:

```text
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-9.1.1, pluggy-1.7.0
collected 32 items

tests/test_app.py::test_api_health_check PASSED                          [  3%]
tests/test_app.py::test_dashboard_and_stats_endpoints PASSED             [  6%]
tests/test_app.py::test_create_list_get_and_delete_website PASSED        [  9%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[not-a-url] PASSED [ 12%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[ftp://example.com/file.txt] PASSED [ 15%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[javascript:alert(1)] PASSED [ 18%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[file:///etc/passwd] PASSED [ 21%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://localhost:8000/admin] PASSED [ 25%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://127.0.0.1/secret] PASSED [ 28%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://127.0.1.1:8080/] PASSED [ 31%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://0.0.0.0/] PASSED [ 34%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://10.0.0.1/internal] PASSED [ 37%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://172.16.0.10/router] PASSED [ 40%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://192.168.1.1/setup] PASSED [ 43%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://169.254.169.254/latest/meta-data/] PASSED [ 46%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://[::1]:8000/] PASSED [ 50%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://intranet.local/dashboard] PASSED [ 53%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[http://service.internal/health] PASSED [ 56%]
tests/test_app.py::test_invalid_and_ssrf_urls_rejected[https://user:password@example.com/private] PASSED [ 59%]
tests/test_app.py::test_check_interval_validation[0] PASSED              [ 62%]
tests/test_app.py::test_check_interval_validation[5] PASSED              [ 65%]
tests/test_app.py::test_check_interval_validation[14] PASSED             [ 68%]
tests/test_app.py::test_check_interval_validation[-10] PASSED            [ 71%]
tests/test_app.py::test_check_interval_validation[1441] PASSED           [ 75%]
tests/test_app.py::test_check_interval_validation[10000] PASSED          [ 78%]
tests/test_app.py::test_initial_snapshot_and_duplicate_snapshot_prevention PASSED [ 81%]
tests/test_app.py::test_detect_genuine_content_change_and_ignore_noise PASSED [ 84%]
tests/test_app.py::test_failed_requests_and_timeout_handling PASSED      [ 87%]
tests/test_app.py::test_redirect_to_internal_ip_is_blocked_by_ssrf_guard PASSED [ 90%]
tests/test_app.py::test_robots_txt_disallow_respected PASSED             [ 93%]
tests/test_app.py::test_diff_generation_and_api_endpoint PASSED          [ 96%]
tests/test_app.py::test_keyword_alerts_and_duplicate_notification_prevention PASSED [100%]
======================= 32 passed in 4.34s =======================
```

---

## 8. REST API Reference

Interactive OpenAPI documentation is available at `/docs`.

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Service health check, DB status, and scheduler state |
| `GET` | `/api/stats` | Aggregated dashboard metrics and 7-day timeline activity |
| `GET` | `/api/sites` | List all monitored websites |
| `POST` | `/api/sites` | Add a new monitored website (validates URL, SSRF & interval) |
| `GET` | `/api/sites/{id}` | Retrieve website details, recent snapshots, and check runs |
| `DELETE` | `/api/sites/{id}` | Delete a website and cascade-delete its snapshots & alerts |
| `POST` | `/api/sites/{id}/check` | Trigger an immediate manual check |
| `GET` | `/api/sites/{id}/history` | Retrieve chronological snapshots and check telemetry |
| `GET` | `/api/sites/{id}/snapshots/{snapshot_id}` | Retrieve full extracted text for a specific snapshot |
| `GET` | `/api/sites/{id}/diff` | Compare two snapshots (`from_id`/`to_id` or `from_version`/`to_version`) |
| `GET` | `/api/sites/{id}/rss` | RSS 2.0 XML feed of detected changes for a website |
| `GET` | `/api/notifications` | List alert history and delivery statuses |

---

## 9. Environment Variable Configuration

All settings are configured via environment variables or `.env` (see `.env.example`):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///./change_detector.db` | SQLAlchemy connection string |
| `REQUEST_TIMEOUT_SECONDS` | `15` | Per-request HTTP timeout in seconds |
| `MAX_REDIRECTS` | `5` | Maximum SSRF-validated redirects to follow |
| `MAX_RESPONSE_BYTES` | `5242880` | Max response body size (5 MB) |
| `RESPECT_ROBOTS_TXT` | `true` | Honor target website `robots.txt` policies |
| `ENABLE_PLAYWRIGHT_FALLBACK` | `false` | Global toggle for headless browser fallback |
| `SCHEDULER_ENABLED` | `true` | Enable background periodic checks |
| `SMTP_ENABLED` | `false` | Enable SMTP email alerts |
| `SMTP_HOST` / `SMTP_PORT` | `smtp.example.com` / `587` | SMTP server host and port |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | Optional SMTP credentials |
| `SMTP_FROM_EMAIL` / `SMTP_TO_EMAIL` | *(empty)* | Sender and recipient email addresses |
| `WEBHOOK_ENABLED` | `false` | Enable Discord / Telegram / Slack / JSON webhook alerts |
| `WEBHOOK_URL` | *(empty)* | Target webhook URL |
| `WEBHOOK_SECRET` | *(empty)* | Optional HMAC-SHA256 signing secret |
| `TELEGRAM_CHAT_ID` | *(empty)* | Optional `chat_id` included for Telegram Bot API webhooks |

---

## 10. UI & Screenshots Overview

- **Dashboard (`/`)**:
  - Top bar with live scheduler health badge, Swagger API Docs shortcut, and `+ Add Website` button.
  - 4 KPI summary cards + 7-day bar chart of detected changes vs. successful/failed checks.
  - Filterable website cards showing check interval, HTTP status, check duration in ms, one-line change summary, and action buttons.
  - Add Website modal with interval preset chips (`15m`, `30m`, `1h`, `6h`, `12h`, `24h`) and SSRF validation hints.
- **Version History & Diff Viewer (`/sites/{id}/history`)**:
  - Interactive version selector dropdowns (`Baseline Version` vs. `Target Version`).
  - Switchable **Side-by-Side Diff**, **Unified Diff**, and **Visual Before & After** tabs with green additions (`+`) and red removals (`-`).
  - Chronological snapshot list with SHA-256 hashes and quick `Compare vX → vY` buttons.

---

## 11. Known Limitations & Future Improvements

- **Single-Process SQLite Concurrency**: SQLite is ideal for lightweight deployments and assessments; for high-throughput multi-worker clusters, switching `DATABASE_URL` to PostgreSQL and using a distributed task queue (e.g., Celery + Redis) is recommended.
- **Text-Focused Diffing**: Content comparison focuses on normalized readable text rather than visual pixel diffs (visual screenshot diffing via Playwright could be added as a future enhancement).
- **Public Unauthenticated Pages Only**: By design, pages behind login walls, CAPTCHAs, or anti-bot challenges are not bypassed.
