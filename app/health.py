import subprocess
import logging
from fastapi import APIRouter

router = APIRouter()
logger = logging.getLogger(__name__)


def get_vram_stats() -> dict:
    # --id=1 targets GPU index 1 (RTX 4070 Ti) by NVML order, matching llama.cpp --main-gpu 1.
    # Verify with: nvidia-smi -L  (GPU 1 must be "NVIDIA GeForce RTX 4070 Ti")
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
        if result.returncode != 0:
            logger.warning("nvidia-smi exited %d: %s", result.returncode, result.stderr.strip())
            return {"error": f"nvidia-smi exit {result.returncode}: {result.stderr.strip()}"}
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
