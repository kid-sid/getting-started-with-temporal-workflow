from agentex.lib.utils.model_utils import BaseModel


class SummarizerState(BaseModel):
    # URLs queued by the user but not yet processed.
    # Cleared to [] after each batch is summarized.
    pending_urls: list[str] = []

    # Total URLs summarized across all batches.
    # Divided by BATCH_SIZE in workflow.py to produce the batch number label.
    total_processed: int = 0

    @classmethod
    def initial(cls) -> "SummarizerState":
        return cls()
