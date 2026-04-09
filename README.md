# SonarAgent

> An autonomous multi-agent system that scans your code with **SonarQube**, generates **LLM-powered fixes**, reviews them for safety, and opens **pull requests** — all from a single dashboard.

SonarAgent connects four cooperating AI agents (**Scanner → Fixer → Reviewer → Reporter**) into a deterministic pipeline. Each stage produces real, persisted artifacts (SonarQube issues, unified-diff patches, confidence-scored reviews, before/after delta reports) that you can browse, retry, or apply with one click.

---

## Table of contents

1. [Highlights](#highlights)
2. [Architecture](#architecture)
3. [Tech stack](#tech-stack)
4. [Prerequisites](#prerequisites)
5. [Quick start](#quick-start)
6. [Configuration](#configuration)
7. [Using SonarAgent](#using-sonaragent)
8. [Pipeline stages explained](#pipeline-stages-explained)
9. [Retrying & re-running specific stages](#retrying--re-running-specific-stages)
10. [Project structure](#project-structure)
11. [API reference](#api-reference)
12. [Troubleshooting](#troubleshooting)
13. [Development](#development)

---

## Highlights

- **Real SonarQube integration** — drives the official `sonar-scanner` CLI, polls the compute-engine for analysis completion, and reads issues via the SonarQube REST API.
- **Per-issue LLM fixes** — generates minimal, targeted unified-diff patches with file context, rule descriptions, and prior agent memory.
- **AI code review** — every fix is independently scored 0–100 by a Reviewer agent that flags regressions and side-effects.
- **One-click apply** — write fixes to disk, force-create a `sonar-fix/<scan-id>` branch, commit, push, and **open a real GitHub pull request**.
- **Per-repo PAT management** — every repo card has an Edit button to update its name, branch, and Personal Access Token. The per-repo PAT always takes precedence over the global default.
- **Full pause / resume / stop** — the pipeline checks for control signals between every issue, so the buttons take effect within seconds, not minutes.
- **Per-stage retry** — when a scan fails or completes, you can re-run just one stage (e.g. regenerate fixes with a new LLM model) without re-cloning or re-scanning.
- **Live application logs** — every backend log line streams to a live `/logs` page in the UI via SSE and is also persisted to a rotating file under `backend/logs/app.log`.
- **In-app credential management** — set the SonarQube URL/token, GitHub PAT, and per-agent LLM model assignments from the **Settings** page; values hot-reload without a backend restart.
- **Pipeline timeouts** — every stage and the pipeline as a whole have configurable timeouts; runaway scans are auto-cancelled with a clear error.
- **Delete + cleanup** — repo deletion can also delete the corresponding SonarQube project (and its scan history) plus the local clone directory in one operation.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         React + Vite UI                          │
│   Dashboard · Logs · AuditLogs · Settings (SonarQube / Agents)   │
└──────────────────────┬───────────────────────────────────────────┘
                       │ REST + WebSocket + SSE
┌──────────────────────▼───────────────────────────────────────────┐
│                      FastAPI Backend                             │
│                                                                  │
│  ┌─────────────────  Pipeline Orchestrator  ────────────────┐    │
│  │                                                          │    │
│  │   Clone   →   Scan   →   Fix   →   Review   →  Report    │    │
│  │     │          │         │          │           │       │    │
│  │     ▼          ▼         ▼          ▼           ▼       │    │
│  │  GitHub    Scanner    Fixer     Reviewer     Reporter   │    │
│  │  Service    Agent     Agent      Agent        Agent     │    │
│  │     │          │         │          │           │       │    │
│  │     ▼          ▼         ▼          ▼           ▼       │    │
│  │  git clone  sonar-     LLM        LLM         LLM        │    │
│  │  /pull/PR   scanner    (per-      review     delta       │    │
│  │             + REST     issue)     score      report      │    │
│  │             API                                          │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Each stage:                                                     │
│   • Persists a PipelineRun row (running/completed/failed)        │
│   • Writes per-action AgentLog rows                              │
│   • Broadcasts structured events to the per-scan WebSocket       │
│   • Honours scan_controller pause/resume/stop checkpoints        │
│                                                                  │
└──────────────┬─────────────────────────────────┬─────────────────┘
               │                                 │
               ▼                                 ▼
        ┌──────────────┐                  ┌──────────────┐
        │  SonarQube   │                  │   GitHub     │
        │  REST + CLI  │                  │   REST API   │
        └──────────────┘                  └──────────────┘
```

### Key design choices

- **Deterministic, not LLM-supervised.** The orchestrator runs the four agents in a fixed sequence as plain Python coroutines. There is no LangGraph "supervisor" deciding the next step at runtime — that approach was tried and removed because it produced unpredictable scans.
- **Idempotent stages with retry-aware skipping.** Each stage records its state in a `PipelineRun` row. When a scan is retried, completed stages with intact artifacts (clone dir on disk, issues in DB, fixes in DB) are skipped automatically — only the failed stage and everything downstream re-run.
- **Per-repo PAT first, global fallback second.** All git operations resolve credentials via `_get_pat_with_source(repo)`, which returns `(token, source_label)`. The label appears in every error message so you know exactly which token to fix.
- **Hot-reloadable credentials.** Settings UI changes write to `backend/.env`, hot-update the in-memory `Settings` singleton, **and** push the new value into `os.environ` — so SDKs that read the process environment directly (Anthropic, Google) see the new value without a restart.

---

## Tech stack

| Layer       | Technology |
|-------------|------------|
| Backend     | FastAPI 0.115 · SQLAlchemy 2 (async) · Alembic · Pydantic v2 |
| Database    | SQLite (default, swap via `DATABASE_URL`) |
| Agents      | LangChain (`langchain-openai`, `langchain-anthropic`, `langchain-google-genai`, `langchain-groq`) |
| Code search | SonarQube REST API + `sonar-scanner` CLI |
| Git         | GitPython · GitHub REST API |
| Frontend    | React 19 · Vite · TanStack Query · TailwindCSS · Zustand |
| Realtime    | WebSocket (per-scan pipeline events) + SSE (global app logs) |
| Logging     | Python `logging` + RotatingFileHandler + in-memory ring buffer |

---

## Prerequisites

| Tool            | Version | Purpose |
|-----------------|---------|---------|
| Python          | 3.11+   | Backend runtime |
| Node.js         | 18+     | Frontend dev server + build |
| Git             | 2.30+   | Cloning repos, branching, pushing |
| `sonar-scanner` | 5.0+    | Local code analysis (otherwise the agent can only fetch existing issues from SonarQube) |
| SonarQube       | 9.9+    | Hosted or self-hosted instance with a User Token |
| GitHub PAT      | —       | Classic with `repo` scope, OR fine-grained with `Contents: R/W` + `Pull requests: R/W` |
| LLM API key     | —       | At least one of OpenAI, Anthropic, Google, or Groq |

### Installing `sonar-scanner`

```bash
# macOS
brew install sonar-scanner

# Linux (Debian/Ubuntu)
sudo apt-get install -y unzip wget
wget https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-5.0.1.3006-linux.zip
unzip sonar-scanner-cli-5.0.1.3006-linux.zip -d /opt
sudo ln -s /opt/sonar-scanner-5.0.1.3006-linux/bin/sonar-scanner /usr/local/bin/sonar-scanner
```

After installing, restart the backend. The **Settings → SonarQube** panel will flip the CLI status badge to **Installed**.

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/<your-username>/SonarAgent.git
cd SonarAgent

# 2. Backend
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                # then edit with your real keys
alembic upgrade head                # creates app.db with the schema

# 3. Frontend (in another shell)
cd ../frontend
npm install
cp .env.example .env.local          # optional, defaults to localhost:8000

# 4. Run (from repo root)
cd ..
chmod +x start.sh
./start.sh
```

Open **http://localhost:5173** and log in with the seeded admin credentials (printed in the backend log on first start).

> 💡 You **don't have to set everything in `.env`** — the Settings UI lets you paste the SonarQube token, edit per-repo GitHub PATs, and assign LLM models to agents at runtime. The `.env` values are just bootstrap defaults.

---

## Configuration

### Required at minimum

| Where | Variable / Setting | Notes |
|-------|--------------------|-------|
| `backend/.env` or **Settings → SonarQube** | `SONARQUBE_URL` + `SONARQUBE_TOKEN` | User token from SonarQube → My Account → Security |
| `backend/.env` | At least one of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `GROQ_API_KEY` | One per provider you assign to an agent |
| **Per-repo Edit modal** | GitHub PAT (preferred) | OR set `GITHUB_DEFAULT_PAT` in `.env` as a fallback |

### Pipeline timeouts (`backend/.env`)

```dotenv
PIPELINE_TOTAL_TIMEOUT_SECONDS=1200    # whole pipeline (default 20 min)
PIPELINE_STAGE_TIMEOUT_SECONDS=600     # each stage (default 10 min)
SONAR_TASK_POLL_TIMEOUT_SECONDS=600    # max wait for sonar-scanner CE task
```

### Default agent → model assignments

```dotenv
SCANNER_AGENT_MODEL=openai/gpt-4o
FIXER_AGENT_MODEL=openai/gpt-4o
REVIEWER_AGENT_MODEL=openai/gpt-4o-mini
REPORTER_AGENT_MODEL=openai/gpt-4o-mini
```

These are fallback defaults — the **Settings → Agent Config** UI lets you override per-agent at runtime.

---

## Using SonarAgent

### 1. Add a repository
- Click **+ Add Repository** on the dashboard
- Paste a GitHub HTTPS URL, set the branch, and paste a PAT (or leave blank to use the global default)
- Click **Create**

### 2. Trigger a scan
- Click **Trigger Scan** on a repo card
- A modal opens showing the live pipeline log, with three tabs:
  - **Live Log** — every agent reasoning step, tool call, and event in real time
  - **Issues** — every SonarQube issue persisted to the DB, with severity badges
  - **Fixes** — every generated fix with its diff, confidence score, and Apply controls

### 3. Apply fixes
- On the **Fixes** tab, click:
  - **Apply locally** — writes fixes to disk + commits on a `sonar-fix/<id>` branch (no push)
  - **Apply & open PR** — also pushes the branch and opens a GitHub pull request
- The success banner shows the branch name, the PAT source used (per-repo vs global), and a clickable link to the PR
- If the PR creation fails (403 / SAML SSO / missing scopes / invalid PAT), the error banner shows GitHub's actual response with an actionable hint

### 4. Pause / resume / stop
- The modal header has **Pause**, **Resume**, **Stop** buttons that take effect within seconds (the orchestrator checks for control signals between every issue)
- The minimize ✕ button is always available — clicking it on a running scan closes the modal but the pipeline keeps running in the background; you can re-open it from the scan history list

### 5. Manage repos
- **Edit** (pencil) — update the repo's display name, branch, or PAT. The new PAT takes effect on the very next scan with no re-clone needed.
- **Delete** (trash) — opens a confirmation modal with optional checkboxes to also delete the SonarQube project + the local clone

### 6. Live application logs
- Click **Logs** in the top nav to see every backend log line in real time, with per-level filters, search, pause, and download
- Logs are also persisted to `backend/logs/app.log` (rotating, 5 MB × 5 backups)

---

## Pipeline stages explained

| Stage     | Agent     | What it does | Artifacts produced |
|-----------|-----------|--------------|--------------------|
| **clone** | (orchestrator) | `git clone` or `git fetch + reset --hard origin/<branch>`. Uses per-repo PAT. | Working directory under `backend/repos/<repo-id>/` |
| **scan**  | Scanner   | Optionally runs `sonar-scanner` CLI against the clone, polls the SonarQube CE task until completion, fetches issues via REST, filters by severity / file exclusions / rule exclusions, and selects the top N for fixing. | `Issue` rows in the DB with `selected_for_fix=true` |
| **fix**   | Fixer     | For each selected issue: reads the source file, extracts ±50 lines of context, fetches the rule HTML description, calls the LLM with prior agent memory, parses the `FIXED_CODE` block, and computes a unified diff. | `Fix` rows with `original_code`, `fixed_code`, `diff_patch`, and `explanation` |
| **review**| Reviewer  | Calls the LLM to score each fix 0-100 for correctness + regression risk. Mutates `Fix.confidence_score` and `Fix.reviewer_summary` in place. | Confidence scores + reviewer summaries |
| **report**| Reporter  | Compares this scan's issue counts against the previous completed scan for the same repo. Generates a narrative delta report via the LLM. | `DeltaReport` row |

Each stage:
- Inserts a `PipelineRun` row with `status=running` at the start, then updates to `completed` / `failed`
- Writes one or more `AgentLog` rows tracking the action, latency, tokens used, and status
- Broadcasts structured events to **two** WebSocket channels: `/ws/pipeline/<scan_id>` (per-scan) and `/ws/logs` (global)
- Calls `scan_controller.checkpoint()` at every iteration so pause/stop takes effect quickly

---

## Retrying & re-running specific stages

Every scan history row in the dashboard has three icons next to it:

| Icon | Action |
|------|--------|
| 🔄 RotateCcw | **Retry from failed stage** — only shown for failed/stopped scans. Resumes from wherever the pipeline broke; previously-completed stages are skipped. |
| 🗂 Layers | **Re-run from stage…** — opens a dropdown listing all 5 stages. Clicking one clears that stage AND every downstream stage (their `PipelineRun` rows + their artifacts) and re-runs them. Works on **any non-running scan** including completed ones — useful for re-running just the Fix stage with a new LLM model. |
| 🗑 Trash | **Delete scan** — drops this scan and all its issues, fixes, and pipeline runs. Stops the pipeline first if it's running. |

The orchestrator's skip-on-completed logic checks both the `PipelineRun` row AND the existence of the artifact (clone dir, issues, fixes) before skipping, so re-runs are always consistent.

---

## Project structure

```
SonarAgent/
├── backend/
│   ├── alembic/              # DB migrations
│   ├── app/
│   │   ├── agents/
│   │   │   ├── orchestrator.py     # Deterministic Clone→Scan→Fix→Review→Report pipeline
│   │   │   ├── scanner.py          # Drives sonar-scanner + REST issue fetch
│   │   │   ├── fixer.py            # Per-issue LLM patch generation
│   │   │   ├── reviewer.py         # Per-fix LLM correctness scoring
│   │   │   ├── reporter.py         # Before/after delta report generation
│   │   │   ├── scan_controller.py  # In-memory pause/resume/stop signals
│   │   │   └── base.py             # Shared LLM call helper + agent memory
│   │   ├── routers/
│   │   │   ├── auth.py             # JWT login/refresh
│   │   │   ├── repos.py            # CRUD + delete with SonarQube cleanup
│   │   │   ├── scans.py            # Trigger / status / pause / stop / retry / rerun-from-stage
│   │   │   ├── fixes.py            # List fixes + apply-fixes (clone→branch→commit→push→PR)
│   │   │   ├── reviews.py          # Per-fix review records
│   │   │   ├── reports.py          # Delta reports
│   │   │   ├── quality_gates.py    # Per-repo severity / file / rule exclusions
│   │   │   ├── settings.py         # SonarQube + LLM provider + agent config endpoints
│   │   │   └── observability.py    # Agent logs + audit trail
│   │   ├── services/
│   │   │   ├── sonarqube.py        # SonarQube REST + scanner CLI wrapper
│   │   │   ├── github.py           # Clone, branch, commit, push, validate_pat, create_pr
│   │   │   ├── llm_router.py       # Maps agent → DB-configured (provider, model) → LangChain
│   │   │   ├── memory.py           # Cross-scan agent memory storage
│   │   │   └── model_fetcher.py    # Fetches model lists from each provider's API
│   │   ├── models/                 # SQLAlchemy ORM models
│   │   ├── schemas/                # Pydantic v2 request/response schemas
│   │   ├── websockets/pipeline.py  # WebSocket connection manager
│   │   ├── log_handler.py          # In-memory ring buffer + RotatingFileHandler
│   │   ├── config.py               # Pydantic Settings (.env loader)
│   │   ├── database.py             # Async SQLAlchemy engine + session factory
│   │   └── main.py                 # FastAPI app factory + lifespan + log handler install
│   ├── logs/                       # Rotating app.log (gitignored)
│   ├── repos/                      # Cloned target repos (gitignored)
│   ├── app.db                      # SQLite database (gitignored)
│   ├── .env                        # Real credentials (gitignored)
│   ├── .env.example                # Template
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx       # Repo cards + scan modal + Issues/Fixes panels
│   │   │   ├── Logs.tsx            # Live SSE-streamed application logs
│   │   │   ├── AuditLogs.tsx       # Historical agent execution traces
│   │   │   ├── Settings.tsx        # SonarQube + LLM Providers + Agent Config tabs
│   │   │   └── Login.tsx
│   │   ├── lib/
│   │   │   ├── api.ts              # Axios client with JWT auto-refresh
│   │   │   └── utils.ts            # cn() class merger
│   │   ├── store/auth.ts           # Zustand auth store
│   │   ├── App.tsx                 # Router + protected routes
│   │   └── main.tsx
│   ├── .env.example
│   ├── package.json
│   └── vite.config.ts
├── start.sh                        # Runs migrations + uvicorn + vite dev
├── .gitignore
├── .env.example                    # Repo-root summary template (real values go in backend/.env)
└── README.md                       # ← you are here
```

---

## API reference

All endpoints are prefixed with `/api`. Authentication is JWT (Bearer token), obtained from `POST /api/auth/login`.

### Auth
| Method | Path | Description |
|--------|------|-------------|
| POST   | `/auth/login`        | Login with email + password |
| POST   | `/auth/refresh`      | Refresh access token |
| GET    | `/auth/me`           | Current user |

### Repositories
| Method | Path | Description |
|--------|------|-------------|
| GET    | `/repos`                 | List repos visible to current user |
| POST   | `/repos`                 | Create a new repo |
| GET    | `/repos/{id}`            | Get one repo |
| PUT    | `/repos/{id}`            | Update name / branch / PAT |
| DELETE | `/repos/{id}`            | Delete repo (`?delete_sonar_project=true&delete_local_clone=true`) |

### Scans
| Method | Path | Description |
|--------|------|-------------|
| POST   | `/scans/repos/{repo_id}/scan`        | Trigger a new scan |
| GET    | `/scans/repos/{repo_id}/scan-history`| List scans for a repo |
| GET    | `/scans/{scan_id}`                   | Single scan status |
| GET    | `/scans/{scan_id}/issues`            | Paginated issues with filters |
| GET    | `/scans/{scan_id}/summary`           | Aggregate: counts, latest error, per-stage status |
| POST   | `/scans/{scan_id}/pause` / `/resume` / `/stop` | Lifecycle controls |
| POST   | `/scans/{scan_id}/retry`             | Retry; `?from_stage=<clone\|scan\|fix\|review\|report>` re-runs from that stage |
| DELETE | `/scans/{scan_id}`                   | Delete a single scan run |

### Fixes
| Method | Path | Description |
|--------|------|-------------|
| GET    | `/scans/{scan_id}/fixes`             | List fixes for a scan (with embedded `issue`) |
| GET    | `/fixes/{fix_id}`                    | Single fix with full diff |
| POST   | `/scans/{scan_id}/apply-fixes`       | `{push_to_github, create_pr}` — clone → branch → commit → push → PR |

### Settings
| Method | Path | Description |
|--------|------|-------------|
| GET    | `/settings/sonarqube`                | Current URL + masked token + CLI status |
| PUT    | `/settings/sonarqube`                | Update SonarQube URL + token (hot-reloads) |
| POST   | `/settings/sonarqube/test`           | Live-test the configured token |
| GET    | `/settings/providers`                | List LLM providers |
| GET    | `/settings/agents`                   | List agent → provider/model assignments |
| PUT    | `/settings/agents/{agent_id}`        | Update an agent's model assignment |
| POST   | `/settings/env`                      | Set any `.env` variable (admin only) |

### Observability
| Method | Path | Description |
|--------|------|-------------|
| GET    | `/observability/logs`                | Historical agent logs with filters |
| GET    | `/api/logs/stream`                   | **SSE** — live tail of every Python log line |
| WS     | `/ws/pipeline/{scan_run_id}`         | Per-scan structured pipeline events |
| WS     | `/ws/logs`                           | Global agent log stream |

---

## Troubleshooting

### "Pipeline error: SonarQube token is not configured"
- Open **Settings → SonarQube**, paste your User token, click **Save Credentials**, then **Test Connection**
- The token must be of type **User Token**, not Project Analysis Token, for the agent to use it across multiple projects

### "GitHub API 403" when clicking Apply & open PR
- The error banner now contains the exact reason. Common causes:
  - **PAT scope is missing.** For classic PATs you need `repo`. For fine-grained PATs you need `Contents: Read & Write` AND `Pull requests: Read & Write`.
  - **SAML SSO not authorized.** On the GitHub PAT page, click "Configure SSO" and authorize for the org.
  - **Wrong PAT user.** The PAT belongs to a user without write access on the target repo. Click the Edit (pencil) icon on the repo card and paste a PAT from a user who has push access.
- The success banner always shows which PAT source was used (`per-repo PAT` vs `.env fallback`), so you know which one to fix.

### Scan completes immediately with 0 issues
- Either the project has no open issues, OR your SonarQube token can't see it (missing **Browse** permission)
- AND `sonar-scanner` CLI is not installed locally, so the agent couldn't trigger a fresh analysis
- Install the CLI (`brew install sonar-scanner`) and re-run

### "address already in use" on port 8000
```bash
lsof -ti:8000 | xargs kill -9
```

### "OPENAI_API_KEY must be set" even though it's in `.env`
- This was a known bug fixed in `app/services/llm_router.py` — the router now reads from `Settings.get_provider_key()` (which loads `.env` via pydantic-settings) instead of `os.environ`. If you're still seeing it, restart the backend so the new code is loaded.

### Pause / Stop button does nothing
- The orchestrator checks for control signals between every issue, so on a long Fixer run there can be a several-second delay before the button takes effect — but it WILL take effect. If it doesn't after ~10 seconds, restart the backend (the in-memory `scan_controller` registry is lost on restart, leaving the scan orphaned).

### "no PAT configured" when applying fixes
- Click the Edit (pencil) icon on the repo card and paste a GitHub PAT
- Or set `GITHUB_DEFAULT_PAT` in `backend/.env` and restart

---

## Development

### Running migrations

```bash
cd backend
source venv/bin/activate
alembic upgrade head                  # apply latest
alembic revision --autogenerate -m "describe change"
```

### Type-checking the frontend

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json
```

### Building for production

```bash
# Frontend
cd frontend
npm run build                         # outputs to dist/

# Backend
# Use a real ASGI server in production:
gunicorn -k uvicorn.workers.UvicornWorker app.main:app -w 4 -b 0.0.0.0:8000
```

### Running tests

```bash
cd backend
source venv/bin/activate
pytest
```

---

## License

This project is provided as-is for educational and internal-tooling purposes. Add your preferred license here.

---

## Acknowledgements

- **SonarQube** for the static analysis backbone
- **LangChain** for unifying the four LLM providers
- **FastAPI** + **React** for the surrounding stack