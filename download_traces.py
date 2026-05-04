"""
Download Temporal workflow execution traces for url-summarizer-temporal.

Connects to the local Temporal server, lists all workflow executions, fetches
the full event history for each one, and saves them as JSON files in ./traces/.

The output JSON has two top-level sections:
  - "conversation" : decoded user messages and agent replies in plain text
  - "events"       : raw Temporal event history (signals, activities, retries)

Usage:
    python download_traces.py              # Download all executions
    python download_traces.py --latest     # Download only the most recent execution
    python download_traces.py --id <id>    # Download one specific workflow by ID
"""

import argparse
import asyncio
import base64
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google.protobuf.json_format import MessageToDict
from temporalio.client import Client, WorkflowExecutionStatus

load_dotenv(Path(__file__).parent / "project" / ".env")

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "localhost:7233")
WORKFLOW_TYPE = "url-summarizer-temporal"
OUTPUT_DIR = Path("traces")

STATUS_LABELS = {
    WorkflowExecutionStatus.RUNNING: "RUNNING",
    WorkflowExecutionStatus.COMPLETED: "COMPLETED",
    WorkflowExecutionStatus.FAILED: "FAILED",
    WorkflowExecutionStatus.CANCELED: "CANCELED",
    WorkflowExecutionStatus.TERMINATED: "TERMINATED",
    WorkflowExecutionStatus.CONTINUED_AS_NEW: "CONTINUED_AS_NEW",
    WorkflowExecutionStatus.TIMED_OUT: "TIMED_OUT",
}


def _decode_payload(payloads: list) -> any:
    """Base64-decode and JSON-parse the first Temporal payload."""
    if not payloads:
        return None
    data = payloads[0].get("data", "")
    if not data:
        return None
    try:
        return json.loads(base64.b64decode(data).decode("utf-8"))
    except Exception:
        return None


def extract_conversation(events: list) -> list:
    """
    Walk the raw event list and pull out human-readable turns in execution order.

    Reads activity_task_scheduled events directly (input payload is there):
      - "create-message" → every chat message (user echo + agent replies)
      - "scrape_url"     → URL being scraped + scraped text preview (from completion)
      - "chat-completion"→ marks where the LLM was called

    Skips internal ADK housekeeping activities (create-state, get-state, update-state).
    """
    # Build a lookup: scheduled_event_id → decoded result payload (for scrape_url)
    completion_results: dict[str, any] = {}
    for event in events:
        completed = event.get("activity_task_completed_event_attributes", {})
        if completed:
            sid = completed.get("scheduled_event_id")
            payloads = completed.get("result", {}).get("payloads", [])
            completion_results[sid] = _decode_payload(payloads)

    turns = []
    for event in events:
        attrs = event.get("activity_task_scheduled_event_attributes", {})
        if not attrs:
            continue

        activity_name = (attrs.get("activity_type") or {}).get("name", "")
        input_payloads = attrs.get("input", {}).get("payloads", [])
        decoded_input = _decode_payload(input_payloads)
        event_id = event.get("event_id")

        if activity_name == "create-message":
            content = (decoded_input or {}).get("content") or {}
            author = content.get("author", "agent")
            text = content.get("content") or content.get("text") or ""
            if text:
                turns.append({
                    "role": author,
                    "event_id": event_id,
                    "text": text,
                })

        elif activity_name == "scrape_url":
            url = (decoded_input or {}).get("url", "")
            result = completion_results.get(event_id, "")
            preview = str(result or "")[:300]
            turns.append({
                "role": "activity:scrape_url",
                "event_id": event_id,
                "url": url,
                "scraped_text_preview": preview + ("…" if len(str(result or "")) > 300 else ""),
            })

        elif activity_name == "chat-completion":
            turns.append({
                "role": "activity:chat-completion",
                "event_id": event_id,
                "note": "LLM called (gpt-4o-mini) — result appears in the next create-message turn",
            })

    return turns


async def download_execution(
    client: Client,
    workflow_id: str,
    run_id: str,
    start_time: datetime | None,
    status: str,
) -> Path:
    handle = client.get_workflow_handle(workflow_id, run_id=run_id)
    history = await handle.fetch_history()

    events = [MessageToDict(e, preserving_proto_field_name=True) for e in history.events]
    conversation = extract_conversation(events)

    ts = start_time.strftime("%Y%m%d_%H%M%S") if start_time else "unknown"
    safe_id = workflow_id[:50].replace("/", "_").replace(":", "_")
    filename = OUTPUT_DIR / f"{ts}_{safe_id}_{run_id[:8]}.json"

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            {
                "workflow_id": workflow_id,
                "run_id": run_id,
                "status": status,
                "start_time": start_time.isoformat() if start_time else None,
                "temporal_ui": (
                    f"http://localhost:8080/namespaces/default/workflows"
                    f"/{workflow_id}/{run_id}/history"
                ),
                "total_events": len(events),
                "conversation": conversation,
                "events": events,
            },
            f,
            indent=2,
        )

    return filename, len(conversation)


async def main(latest_only: bool, specific_id: str | None) -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Connecting to Temporal at {TEMPORAL_ADDRESS}...")
    client = await Client.connect(TEMPORAL_ADDRESS)
    print(f"Connected. Fetching executions for workflow type: {WORKFLOW_TYPE}\n")

    query = f'WorkflowType="{WORKFLOW_TYPE}"'
    if specific_id:
        query += f' AND WorkflowId="{specific_id}"'

    executions = []
    async for ex in client.list_workflows(query=query):
        executions.append(ex)

    if not executions:
        print("No workflow executions found.")
        return

    targets = executions[:1] if latest_only else executions
    print(f"Found {len(executions)} execution(s). Downloading {len(targets)}...\n")

    for i, ex in enumerate(targets, 1):
        status = STATUS_LABELS.get(ex.status, "UNKNOWN")
        print(f"[{i}/{len(targets)}] {ex.id}")
        print(f"         run_id : {ex.run_id}")
        print(f"         status : {status}")
        print(f"         started: {ex.start_time}")

        try:
            path, n_turns = await download_execution(client, ex.id, ex.run_id, ex.start_time, status)
            print(f"         saved  : {path}  ({n_turns} conversation turns decoded)")
        except Exception as e:
            print(f"         ERROR  : {e}")
        print()

    print(f"Done. Traces saved to ./{OUTPUT_DIR}/")
    print(f"Open http://localhost:8080 to browse them interactively.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Temporal traces for url-summarizer-temporal")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--latest", action="store_true", help="Download only the most recent execution")
    group.add_argument("--id", dest="workflow_id", metavar="WORKFLOW_ID", help="Download a specific workflow by ID")
    args = parser.parse_args()

    asyncio.run(main(latest_only=args.latest, specific_id=args.workflow_id))
