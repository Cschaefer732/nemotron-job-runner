import logging
import os
from pathlib import Path
from app.handlers import JobHandler

logger = logging.getLogger(__name__)

# Vault path driven by env var — falls back to the known local path.
# Override with: set NEMOTRON_VAULT_PATH=E:\path\to\vault
VAULT_PATH = Path(os.environ.get("NEMOTRON_VAULT_PATH", r"E:\ObsidianVault\AdminAssistantMemory"))
SENTINEL_FILE = VAULT_PATH / ".smart-env" / "reembed_trigger"


class SmartConnectionsReembed(JobHandler):
    """Trigger Smart Connections re-embedding of the Obsidian vault."""

    job_type = "smart_connections_reembed"

    def run(self, job_id: str, payload: dict) -> dict:
        """
        Write a sentinel file to trigger Smart Connections re-embedding.
        The Smart Connections plugin must be configured to watch for this file.
        If the sentinel approach is not supported, swap in an Obsidian URI call here.
        """
        if not VAULT_PATH.exists():
            raise RuntimeError(
                f"Vault path does not exist: {VAULT_PATH}. "
                "Set NEMOTRON_VAULT_PATH env var to the correct location."
            )
        logger.info("Triggering Smart Connections re-embed (job %s)", job_id)
        SENTINEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        SENTINEL_FILE.touch()
        logger.info("Wrote sentinel file: %s", SENTINEL_FILE)
        return {"sentinel_file": str(SENTINEL_FILE), "triggered": True}
