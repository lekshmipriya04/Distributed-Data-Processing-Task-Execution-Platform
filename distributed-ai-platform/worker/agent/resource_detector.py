"""Detects hardware resources on the current machine."""
import platform
import structlog

logger = structlog.get_logger(__name__)


def detect_resources() -> dict:
    """Detect CPU, RAM, and GPU resources."""
    import psutil
    
    cpu_count = psutil.cpu_count(logical=True)
    memory_gb = psutil.virtual_memory().total / (1024 ** 3)
    
    # GPU detection
    gpu_count = 0
    gpu_model = None
    gpu_memory_gb = 0.0
    
    try:
        import GPUtil
        gpus = GPUtil.getGPUs()
        if gpus:
            gpu_count = len(gpus)
            gpu_model = gpus[0].name
            gpu_memory_gb = gpus[0].memoryTotal / 1024
    except ImportError:
        # Try nvidia-smi via pynvml
        try:
            import pynvml
            pynvml.nvmlInit()
            gpu_count = pynvml.nvmlDeviceGetCount()
            if gpu_count > 0:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                gpu_model = pynvml.nvmlDeviceGetName(handle).decode("utf-8")
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpu_memory_gb = mem_info.total / (1024 ** 3)
        except Exception:
            pass

    resources = {
        "cpu_total": cpu_count,
        "memory_total_gb": round(memory_gb, 2),
        "gpu_count": gpu_count,
        "gpu_model": gpu_model,
        "gpu_memory_gb": round(gpu_memory_gb, 2),
        "os": platform.system(),
        "os_version": platform.version(),
    }
    
    logger.info("resources_detected", **resources)
    return resources


def get_available_resources() -> dict:
    """Get currently available (free) resources."""
    import psutil
    
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_available = max(1, int(psutil.cpu_count(logical=True) * (1 - cpu_percent / 100)))
    memory = psutil.virtual_memory()
    memory_available_gb = round(memory.available / (1024 ** 3), 2)
    
    gpu_available = False
    try:
        import GPUtil
        gpus = GPUtil.getGPUs()
        if gpus and gpus[0].memoryUtil < 0.5:
            gpu_available = True
    except Exception:
        pass
    
    return {
        "cpu_available": cpu_available,
        "memory_available_gb": memory_available_gb,
        "gpu_available": gpu_available,
    }
