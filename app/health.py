import subprocess
import logging
from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)


def get_vram_stats() -> dict:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
                "--id=1",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        used, free, total = [int(x.strip()) for x in result.stdout.strip().split(",")]
        return {"used_mb": used, "free_mb": free, "total_mb": total, "gpu": "RTX 4070 Ti"}
    except Exception as exc:
        logger.warning("nvidia-smi failed: %s", exc)
        return {"error": str(exc)}


@router.get("/health")
def health():
    return {
        "status": "ok",
        "vram": get_vram_stats(),
    }
