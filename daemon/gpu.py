"""Give cached GPU memory back (PyTorch keeps freed blocks reserved, so VRAM never shrinks when a tab closes)."""
import gc
import sys


def free_gpu_cache() -> bool:
    """Collect garbage and release PyTorch's unused cached VRAM. True if a CUDA cache was emptied; never raises, never loads torch."""
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        gc.collect()
        if not torch.cuda.is_available():
            return False
        torch.cuda.empty_cache()
        return True
    except Exception:
        return False
