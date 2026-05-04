import asyncio
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

from agentex.lib.core.temporal.activities import get_all_activities
from agentex.lib.core.temporal.workers.worker import AgentexWorker
from agentex.lib.environment_variables import EnvironmentVariables
from agentex.lib.utils.debug import setup_debug_if_enabled
from agentex.lib.utils.logging import make_logger

from project.activities import ScraperActivities
from project.workflow import URLSummarizerWorkflow

environment_variables = EnvironmentVariables.refresh()
logger = make_logger(__name__)


async def main() -> None:
    setup_debug_if_enabled()

    task_queue = environment_variables.WORKFLOW_TASK_QUEUE
    if not task_queue:
        raise ValueError("WORKFLOW_TASK_QUEUE is not set")

    scraper = ScraperActivities()

    worker = AgentexWorker(task_queue=task_queue, health_check_port=8084)
    await worker.run(
        # get_all_activities() = built-in ADK activities (messages, state, tracing, etc.)
        # scraper.scrape_url  = the custom URL scraping activity
        activities=[*get_all_activities(), scraper.scrape_url],
        workflow=URLSummarizerWorkflow,
    )


if __name__ == "__main__":
    asyncio.run(main())
