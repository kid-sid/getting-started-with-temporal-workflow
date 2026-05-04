import os
from pathlib import Path

from dotenv import load_dotenv
# Load project/.env for local development (OPENAI_API_KEY, TEMPORAL_ADDRESS, etc.)
# When running via `agentex agents run`, these are injected from manifest.yaml instead.
load_dotenv(Path(__file__).parent / ".env", override=True)

from agentex.lib.sdk.fastacp.fastacp import FastACP
from agentex.lib.types.fastacp import TemporalACPConfig

# FastACP entry point — the Agentex platform sends JSON-RPC calls here.
#
# acp_type="async" + TemporalACPConfig means ALL three ACP hooks are handled
# automatically by the Temporal workflow (no @acp.on_task_create etc. needed here):
#
#   task/create  → starts URLSummarizerWorkflow via Temporal client
#   event/send   → sends a RECEIVE_EVENT signal to the running workflow
#   task/cancel  → cancels the Temporal workflow execution directly
acp = FastACP.create(
    acp_type="async",
    config=TemporalACPConfig(
        type="temporal",
        temporal_address=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
    ),
)
