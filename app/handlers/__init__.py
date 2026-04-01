import importlib
import inspect
import pkgutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class JobHandler:
    """Base class for all job handlers. Subclasses must set job_type and implement run()."""
    job_type: str = ""

    def run(self, job_id: str, payload: dict) -> dict:
        raise NotImplementedError


def load_handlers() -> dict[str, JobHandler]:
    """
    Scan all modules in app/handlers/ for JobHandler subclasses.
    Returns a dict mapping job_type -> handler instance.
    Skips the base JobHandler class itself.
    """
    handlers: dict[str, JobHandler] = {}
    package_dir = Path(__file__).parent
    package_name = __name__  # "app.handlers"

    for _, module_name, _ in pkgutil.iter_modules([str(package_dir)]):
        full_name = f"{package_name}.{module_name}"
        try:
            module = importlib.import_module(full_name)
        except Exception:
            logger.exception("Failed to import handler module %s", full_name)
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, JobHandler)
                and obj is not JobHandler
                and obj.job_type
            ):
                instance = obj()
                handlers[instance.job_type] = instance
                logger.debug("Registered handler: %s", instance.job_type)

    return handlers
