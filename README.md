# Getting Started with Temporal Workflows

A hands-on example of building a durable AI agent with Temporal — batches user-submitted URLs, scrapes each page, and returns LLM summaries using the Agentex platform.

**Stack:** FastAPI · Next.js · Temporal · MongoDB · PostgreSQL · Redis · GPT-4o-mini

---

## What It Does

Send URLs to the agent one by one. Once it has collected a batch of 2, it:
1. Scrapes each URL (via a Temporal activity with auto-retry)
2. Sends the combined page text to GPT-4o-mini
3. Returns a structured summary with an overview and 3 key points per URL

The workflow stays alive between messages using Temporal signals — surviving crashes and restarts without losing state.

---

## Project Structure

```
├── project/
│   ├── acp.py          # FastACP entry point (JSON-RPC → Temporal)
│   ├── workflow.py     # Main Temporal workflow — all agent logic
│   ├── activities.py   # URL scraping activity (httpx + BeautifulSoup)
│   ├── models.py       # SummarizerState persisted in MongoDB
│   └── run_worker.py   # Temporal worker — registers workflow + activities
├── manifest.yaml       # Agent declaration (name, queue, ports)
├── docker-compose.yml  # Full infrastructure stack
├── download_traces.py  # Script to export workflow traces as JSON
└── build.ps1           # Windows dev commands
```

---

## Getting Started

### Prerequisites

- Docker Desktop
- Python 3.12+
- Node.js 18+
- [uv](https://github.com/astral-sh/uv)
- An OpenAI API key

### 1. Add your API key

Edit `manifest.yaml`:
```yaml
env:
  OPENAI_API_KEY: "sk-..."
```

### 2. Start infrastructure (Terminal 1)

```powershell
.\build.ps1 dev
```

Starts: Temporal, PostgreSQL, Redis, MongoDB, OpenTelemetry collector, Agentex backend.

### 3. Start the agent worker (Terminal 2)

```powershell
.\build.ps1 agent
```

Starts the FastACP HTTP server and Temporal worker polling `url_summarizer_temporal_queue`.

### 4. Start the UI (Terminal 3)

```powershell
.\build.ps1 ui
```

---

## Usage

1. Open **http://localhost:3000**
2. Find the `url-summarizer-temporal` agent and create a task
3. Send a URL — the agent will ask for one more to complete the batch
4. Send a second URL — it scrapes both and returns a summary

---

## Observability

| Tool | URL | Purpose |
|------|-----|---------|
| Agentex UI | http://localhost:3000 | Chat interface |
| Temporal Web UI | **http://localhost:8080** | Workflow traces, signals, activity retries |
| FastAPI docs | http://localhost:5003/swagger | Backend API |

### Download traces as JSON

```powershell
python download_traces.py --latest        # most recent execution
python download_traces.py --id <wf-id>   # specific workflow
python download_traces.py                 # all executions
```

Each file includes a decoded `conversation` section (user messages + agent replies in order) and the raw Temporal event history.

---

## How It Works

```
User message → Agentex backend → FastACP (acp.py)
    → Temporal RECEIVE_EVENT signal
        → on_task_event_send() in workflow.py
            → state loaded from MongoDB
            → URL queued; if batch full:
                → scrape_url activity (httpx + BeautifulSoup)
                → GPT-4o-mini via litellm
                → summary sent to chat UI
            → state saved back to MongoDB
```

The workflow suspends at `wait_condition` between messages — no polling, no threads, just Temporal's durable execution model.

---

## Key Concepts Demonstrated

- **Temporal signals** — delivering user messages to a running workflow
- **Temporal activities** — isolating I/O (HTTP scraping) with automatic retry
- **Durable state** — `SummarizerState` in MongoDB survives worker restarts
- **Batch accumulation** — collecting multiple inputs across turns before acting
- **ACP protocol** — Agentex's JSON-RPC layer for agent communication
