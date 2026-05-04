# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

Agentex is a platform for building and deploying intelligent agents at any autonomy level (L1–L5). This repo contains:

- **agentex/** — FastAPI backend + Temporal workflow worker
- **agentex-ui/** — Next.js 15 developer UI
- **sync-agent/**, **async-agent/**, **procurement-agent/** — Original ACP agent examples
- **agent-chat/**, **url-summarizer/**, **state_machine/**, **temporal-agent/** — Newer ACP agent examples (Temporal-based)
- **money-transfer/**, **temporal-2/** — Raw Temporal workflow examples (no ACP, for learning Temporal primitives)
- **python-agentex-reference/scale-agentex-python/** — Local checkout of the agentex-python SDK

The platform integrates with the [agentex-python SDK](https://github.com/scaleapi/scale-agentex-python) for agent development.

---

## Development Setup

### Windows (this machine)

Use `build.ps1` in place of `make` for all backend and frontend commands. See [WINDOWS.md](WINDOWS.md) for the full reference.

```powershell
# Activate venv in agentex/
.venv\Scripts\Activate.ps1

# Backend dev server
cd agentex && .\build.ps1 dev

# Frontend dev server
cd agentex-ui && .\build.ps1 dev
```

Set the execution policy once if scripts are blocked:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Quick Start (macOS/Linux)

```bash
./dev.sh          # Installs prerequisites, starts backend + frontend
./dev.sh stop|status|logs|restart
```

### Manual Start (3 terminals)

```bash
# Terminal 1 — backend (starts Docker services + FastAPI)
cd agentex && make dev

# Terminal 2 — frontend
cd agentex-ui && npm install && npm run dev

# Terminal 3 — run a local agent
export ENVIRONMENT=development
agentex agents run --manifest manifest.yaml
```

### Docker Services (started by `make dev`)

| Port | Service |
|------|---------|
| 5003 | FastAPI backend |
| 5432 | PostgreSQL (app) |
| 5433 | PostgreSQL (Temporal) |
| 6379 | Redis |
| 27017 | MongoDB |
| 7233 | Temporal server |
| 8080 | Temporal Web UI |
| 3000 | Next.js UI |

---

## Common Commands

### Backend (`agentex/`)

```bash
make install-dev                        # Install deps + pre-commit hooks
make dev / dev-stop / dev-wipe          # Docker lifecycle
make migration NAME="description"       # Create Alembic migration
make apply-migrations                   # Apply pending migrations
make test                               # All tests
make test FILE=tests/unit/test_foo.py   # Single file
make test NAME=crud                     # Pattern match
make test-unit / test-integration / test-cov
uv run ruff check src/ --fix            # Lint + autofix
uv run ruff format src/                 # Format
```

### Frontend (`agentex-ui/`)

```bash
npm run dev / build / typecheck / lint / format
npm test                                # Vitest
```

### Agent SDK

```bash
export ENVIRONMENT=development
agentex init                            # Scaffold new agent
agentex agents run --manifest manifest.yaml         # Run locally
agentex agents run --manifest manifest.yaml --debug-worker           # Debug mode
agentex agents run --manifest manifest.yaml --debug-worker --debug-port 5679
agentex agents build --manifest manifest.yaml --push
agentex agents deploy --manifest manifest.yaml
```

### SDK Reference (python-agentex-reference/)

```bash
cd python-agentex-reference/scale-agentex-python
rye sync --all-features    # Install deps (uses rye, not uv)
rye run pytest             # Run tests
rye run format             # Format (scope to changed files to avoid noise)
rye run lint
rye run typecheck          # Runs both pyright and mypy
```

Tutorials inside `examples/tutorials/` each have their own virtualenv and use `uv run` instead of `rye run`.

---

## Backend Architecture

The backend (`agentex/src/`) follows strict DDD layering. Dependencies flow inward: **API → Domain ← Adapters**.

### Layer responsibilities

- **`api/`** — FastAPI routes, middleware, Pydantic request/response schemas. Never import ORM models here.
- **`domain/entities/`** — Pure Pydantic models, zero framework imports.
- **`domain/use_cases/`** — Orchestrate domain services; no HTTP exceptions here.
- **`domain/repositories/`** — Abstract interfaces; adapters implement them.
- **`adapters/`** — Postgres/MongoDB/Redis/Temporal/HTTP. ORM ↔ domain converters live here.
- **`config/dependencies.py`** — `GlobalDependencies` singleton: Temporal client, DB engines, Redis.

### Entity conversion

Three distinct representations; never skip a layer:
1. ORM (`adapters/orm.py`) → domain entity via `convert_*_to_entity()` in the adapter
2. Domain entity → API response schema in the route handler

### Exception mapping

| Layer | Raise | HTTP result |
|-------|-------|-------------|
| Domain | `ClientError` | 400 |
| Domain | `ServiceError` | 500 |
| CRUD adapter | `ItemDoesNotExist` | 404 |
| ACP protocol | `JSONRPCError` | — |

Never raise `HTTPException` inside a use case.

### Temporal

- Workflows and activities in `src/temporal/` (outside the domain layer)
- Separate Docker service: `agentex-temporal-worker`
- Task queue: `AGENTEX_SERVER_TASK_QUEUE` env var
- Temporal UI: http://localhost:8080

### Frontend hooks pattern

`hooks/` fetch initial state with React Query, then open an SSE connection for real-time deltas from Redis streams. Message streaming uses client-side delta aggregation.

---

## Agent Types (ACP)

Three types; choose based on execution model:

| Type | Handler decorator | Use when |
|------|------------------|----------|
| **Sync** | `@acp.on_message_send` | Simple request/response, one request at a time |
| **Async** | `@acp.on_task_create`, `on_task_event_send`, `on_task_cancel` | Stateful, multi-turn, concurrent |
| **Temporal** | Async ACP + Temporal workflow | Long-running, durable, human-in-the-loop |

See examples: `sync-agent/agentex_demo/`, `async-agent/project/`, `procurement-agent/project/`.

The `manifest.yaml` in each agent directory controls `acp_type`, Temporal enablement, credentials, env vars, and Docker build context. For local dev, `local_development.paths.acp` points to the `acp.py` entry point.

### Temporal agent structure

All Temporal agents extend `BaseWorkflow` from `agentex.lib.core.temporal.workflows.workflow`:

```python
@workflow.defn(name=environment_variables.WORKFLOW_NAME)
class MyWorkflow(BaseWorkflow):
    def __init__(self):
        super().__init__(display_name="My Agent")
        self._done = False

    @workflow.signal(name=SignalName.RECEIVE_EVENT)
    @override
    async def on_task_event_send(self, params: SendEventParams) -> None:
        # Handles each user message (turn)
        ...

    @workflow.run
    @override
    async def on_task_create(self, params: CreateTaskParams) -> None:
        # Called once when task is created; keep alive with wait_condition
        await workflow.wait_condition(lambda: self._done)
```

`acp.py` for Temporal agents only needs `FastACP.create(acp_type="async", config=TemporalACPConfig(...))` — no handler registration needed (they're auto-registered).

---

## ADK (Agent Development Kit)

Agents interact with the platform through `agentex.lib.adk`, imported as:

```python
from agentex.lib import adk
```

Key namespaces:

| Module | Purpose |
|--------|---------|
| `adk.messages.create(task_id, content)` | Send a message to the user |
| `adk.state.create/get_by_task_and_agent/update` | MongoDB-backed per-task state |
| `adk.tracing.start_span / span (context manager)` | Distributed tracing |
| `adk.providers.litellm.chat_completion(llm_config, trace_id)` | LLM via litellm |
| `adk.providers.openai.run_agent_streamed_auto_send(...)` | OpenAI Agents SDK integration |
| `adk.tasks`, `adk.events`, `adk.streaming` | Task lifecycle helpers |

`LLMConfig` (from `agentex.lib.types.llm_messages`) holds model + message list for litellm calls.

---

## State Machine Pattern

For workflows with complex branching logic, use `agentex.lib.sdk.state_machine`:

```python
from agentex.lib.sdk.state_machine.state import State

self.state_machine = MyStateMachine(
    initial_state=MyState.WAITING,
    states=[
        State(name=MyState.WAITING, workflow=WaitingWorkflow()),
        State(name=MyState.PROCESSING, workflow=ProcessingWorkflow()),
    ],
    state_machine_data=MyData(),
    trace_transitions=True,
)

# In on_task_create:
await self.state_machine.run()

# Trigger transitions from signals:
await self.state_machine.transition(MyState.PROCESSING)
```

See `state_machine/project/` for a full deep-research example with three states (`WAITING_FOR_USER_INPUT`, `CLARIFYING_USER_QUERY`, `PERFORMING_DEEP_RESEARCH`).

---

## Example Agents Reference

| Directory | Type | Pattern demonstrated |
|-----------|------|---------------------|
| `sync-agent/` | Sync ACP | Simple request/response |
| `async-agent/` | Async ACP | Multi-turn stateful |
| `procurement-agent/` | Async + Temporal | Human-in-the-loop approval |
| `agent-chat/` | Async + Temporal | OpenAI Agents SDK + MCP tools + calculator |
| `url-summarizer/` | Async + Temporal | Batched URL scraping + litellm summarization + `adk.state` |
| `state_machine/` | Async + Temporal | State machine pattern, deep research workflow |
| `temporal-agent/` | Async + Temporal | General demo: state, signals, activities |
| `money-transfer/` | Raw Temporal | Saga pattern (withdraw → deposit → refund) |
| `temporal-2/` | Raw Temporal | Minimal activity + workflow skeleton |

`money-transfer/` and `temporal-2/` are pure Temporal learning examples — they have no ACP layer and cannot be run with `agentex agents run`.

---

## Adding New Features

### New API endpoint (full stack)

1. `domain/entities/` — Pydantic entity
2. `domain/repositories/` — abstract interface
3. `adapters/crud_store/` — ORM model + `convert_*_to_entity()`
4. `domain/use_cases/` — business logic
5. `api/schemas/` — request/response schemas
6. `api/routes/` — route; register in `api/app.py`
7. Tests in `tests/unit/` and `tests/integration/`

### New database table

1. SQLAlchemy model in `database/models/` (use JSONB for flexible metadata)
2. `make migration NAME="..."` → review generated file → `make apply-migrations`

### New MongoDB collection

1. Define indexes in `src/config/mongodb_indexes.py` (auto-created on startup)
2. Domain entity + CRUD in `src/adapters/crud_store/adapter_mongodb.py`

---

## Important Notes

### Environment variables

Always set for local agent development:
```bash
export ENVIRONMENT=development
```

Backend reads `DATABASE_URL`, `TEMPORAL_ADDRESS`, `REDIS_URL`, `MONGODB_URI`, `MONGODB_DATABASE_NAME`. Defaults are in `agentex/docker-compose.yml`.

### Pre-commit hooks

Installed by `make install-dev`. Run manually: `pre-commit run --all-files` from `agentex/`.

- Python: ruff lint + format
- TypeScript/JSX: ESLint (Next.js config)

### Redis port conflict

Stop local Redis before starting Docker services (port 6379):
```bash
brew services stop redis          # macOS
sudo systemctl stop redis-server  # Linux
Stop-Service redis                # Windows
```

### API docs (local)

- Swagger: http://localhost:5003/swagger
- ReDoc: http://localhost:5003/api
- OpenAPI JSON: http://localhost:5003/openapi.json
