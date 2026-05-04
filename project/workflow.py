from datetime import timedelta
from typing import override

from temporalio import workflow
from temporalio.common import RetryPolicy

from agentex.lib import adk
from agentex.lib.core.temporal.types.workflow import SignalName
from agentex.lib.core.temporal.workflows.workflow import BaseWorkflow
from agentex.lib.environment_variables import EnvironmentVariables
from agentex.lib.types.acp import CreateTaskParams, SendEventParams
from agentex.lib.types.llm_messages import LLMConfig, SystemMessage, UserMessage
from agentex.lib.utils.logging import make_logger
from agentex.types.text_content import TextContent

from project.activities import SCRAPE_URL_ACTIVITY, ScrapeURLParams, extract_url
from project.models import SummarizerState

environment_variables = EnvironmentVariables.refresh()

if not environment_variables.WORKFLOW_NAME:
    raise ValueError("WORKFLOW_NAME env var is not set")

logger = make_logger(__name__)

# Trigger scraping + summarization once this many URLs have been collected.
BATCH_SIZE = 2

SYSTEM_PROMPT = (
    "You are a helpful assistant that summarizes web pages clearly and concisely. "
    "For each URL, write: a one-sentence overview and 3 key points. Use plain language."
    "Give the agentex temporal UI URL: `http://localhost:8080` to view the conversation history and debug logs after each batch is processed."
)

@workflow.defn(name=environment_variables.WORKFLOW_NAME)
class URLSummarizerWorkflow(BaseWorkflow):

    def __init__(self):
        super().__init__(display_name="URL Summarizer")
        # Stays False forever — workflow lives until Temporal cancels it externally.
        self._done = False

    # ── Entry point ────────────────────────────────────────────────────────────
    # Called once when the task is first created.
    @workflow.run
    @override
    async def on_task_create(self, params: CreateTaskParams) -> None:
        # Seed an empty state document in MongoDB for this task.
        await adk.state.create(
            task_id=params.task.id,
            agent_id=params.agent.id,
            state=SummarizerState.initial(),
        )

        # Opening message shown in the chat UI.
        await adk.messages.create(
            task_id=params.task.id,
            content=TextContent(
                author="agent",
                content=(
                    f"Hi! Send me URLs one by one. "
                    f"I'll summarize them in batches of {BATCH_SIZE}.\n\n"
                    f"Send URL 1/{BATCH_SIZE} to get started."
                ),
            ),
        )

        # Block here so the workflow stays alive to receive future signals.
        # Temporal evaluates this lambda after every signal delivery.
        await workflow.wait_condition(lambda: self._done)

    # ── Signal handler ─────────────────────────────────────────────────────────
    # Called every time the user sends a message to this task.
    @workflow.signal(name=SignalName.RECEIVE_EVENT)
    @override
    async def on_task_event_send(self, params: SendEventParams) -> None:
        # Only handle plain-text messages.
        if not params.event.content or params.event.content.type != "text":
            return

        user_text = params.event.content.content or ""
        task_id = params.task.id
        agent_id = params.agent.id

        # Mirror the user's message in the chat (not done automatically).
        await adk.messages.create(task_id=task_id, content=params.event.content)

        # Look for an https?:// URL in the message.
        url = extract_url(user_text)

        if not url:
            await adk.messages.create(
                task_id=task_id,
                content=TextContent(
                    author="agent",
                    content="I couldn't find a URL. Please send one starting with https://",
                ),
            )
            return

        # Load current state from MongoDB.
        task_state = await adk.state.get_by_task_and_agent(task_id=task_id, agent_id=agent_id)
        state = SummarizerState.model_validate(task_state.state)

        # Queue the URL and tell the user its position in the batch.
        state.pending_urls.append(url)
        slot = len(state.pending_urls)

        await adk.messages.create(
            task_id=task_id,
            content=TextContent(author="agent", content=f"Added URL {slot}/{BATCH_SIZE}: `{url}`"),
        )

        # Batch not full yet — save and wait for more.
        if len(state.pending_urls) < BATCH_SIZE:
            remaining = BATCH_SIZE - len(state.pending_urls)
            await adk.messages.create(
                task_id=task_id,
                content=TextContent(
                    author="agent",
                    content=f"Send {remaining} more URL{'s' if remaining > 1 else ''} to trigger the summary.",
                ),
            )
            await adk.state.update(
                state_id=task_state.id,
                task_id=task_id,
                agent_id=agent_id,
                state=state,
            )
            return

        # ── Batch is full: scrape then summarize ───────────────────────────────

        urls_to_process = list(state.pending_urls)
        state.pending_urls = []          # Clear now so next batch starts fresh
        state.total_processed += BATCH_SIZE

        await adk.messages.create(
            task_id=task_id,
            content=TextContent(author="agent", content=f"Batch complete! Scraping {BATCH_SIZE} URLs..."),
        )

        # Scrape each URL via a Temporal activity (real I/O lives there, not here).
        scraped_pages = []
        for u in urls_to_process:
            await adk.messages.create(
                task_id=task_id,
                content=TextContent(author="agent", content=f"Scraping `{u}`..."),
            )
            try:
                page_text: str = await workflow.execute_activity(
                    SCRAPE_URL_ACTIVITY,
                    ScrapeURLParams(url=u),
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                scraped_pages.append((u, page_text))
            except Exception as e:
                await adk.messages.create(
                    task_id=task_id,
                    content=TextContent(author="agent", content=f"Failed to scrape `{u}`: {e}"),
                )

        if not scraped_pages:
            await adk.messages.create(
                task_id=task_id,
                content=TextContent(author="agent", content="All URLs failed to scrape."),
            )
            await adk.state.update(
                state_id=task_state.id, task_id=task_id, agent_id=agent_id, state=state
            )
            return

        # Build a single prompt containing all scraped pages (4000 chars each).
        combined_content = ""
        for u, text in scraped_pages:
            combined_content += f"\n\n--- URL: {u} ---\n{text[:4000]}"

        await adk.messages.create(
            task_id=task_id,
            content=TextContent(author="agent", content="Summarizing..."),
        )

        # Send to LLM via litellm.
        chat_completion = await adk.providers.litellm.chat_completion(
            llm_config=LLMConfig(
                model="gpt-4o-mini",
                messages=[
                    SystemMessage(content=SYSTEM_PROMPT),
                    UserMessage(
                        content=(
                            f"Summarize each of the following {len(scraped_pages)} web pages separately:\n"
                            f"{combined_content}"
                        )
                    ),
                ],
            ),
            trace_id=task_id,
        )

        summary = ""
        if chat_completion.choices and chat_completion.choices[0].message:
            summary = chat_completion.choices[0].message.content or ""

        batch_num = state.total_processed // BATCH_SIZE
        await adk.messages.create(
            task_id=task_id,
            content=TextContent(
                author="agent",
                content=f"**Batch {batch_num} Summary ({len(scraped_pages)} URLs):**\n\n{summary}",
            ),
        )

        await adk.messages.create(
            task_id=task_id,
            content=TextContent(author="agent", content="Ready for the next batch! Send 2 more URLs."),
        )

        await adk.messages.create(
            task_id=task_id,
            content=TextContent(
                author="agent",
                content=f"View the full trace for this batch (signals, activities, retries) in the Temporal UI: http://localhost:8080",
            ),
        )

        # Persist cleared state for the next batch.
        await adk.state.update(
            state_id=task_state.id, task_id=task_id, agent_id=agent_id, state=state
        )
