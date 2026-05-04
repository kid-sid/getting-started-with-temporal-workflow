import re
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel
from temporalio import activity

from agentex.lib.utils.logging import make_logger

logger = make_logger(__name__)

# Matches the @activity.defn name below — used in workflow.execute_activity().
SCRAPE_URL_ACTIVITY = "scrape_url"

# Regex to find the first http/https URL in a string.
# Stops at whitespace and common trailing punctuation so "see https://x.com." works.
URL_PATTERN = re.compile(r"https?://[^\s\"'>]+")


def extract_url(text: str) -> str | None:
    """Return the first URL found in text, or None."""
    match = URL_PATTERN.search(text)
    return match.group(0).rstrip(".,)") if match else None


class ScrapeURLParams(BaseModel):
    """Input for the scrape_url activity. Serialized to JSON by Temporal."""
    url: str


class ScraperActivities:
    @activity.defn(name=SCRAPE_URL_ACTIVITY)
    async def scrape_url(self, params: ScrapeURLParams) -> str:
        """
        Fetch a URL and return its visible text content (capped at 8000 chars).
        Runs in the worker process where real I/O is allowed.
        Temporal retries this on failure per the RetryPolicy in the workflow.
        """
        logger.info(f"Scraping: {params.url}")

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.get(
                params.url,
                # Avoids 403s from sites that block the default python-httpx UA.
                headers={"User-Agent": "Mozilla/5.0 (compatible; url-summarizer/1.0)"},
            )
            response.raise_for_status()  # Non-2xx → exception → Temporal retries

        soup = BeautifulSoup(response.text, "html.parser")

        # Strip tags with no article content so the LLM sees clean text.
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
        text = "\n".join(line for line in lines if line)

        return text[:8000]  # Safety cap before returning to the workflow
