from langchain.tools import tool


@tool
def get_system_stats() -> str:
    """
    Get real-time CPU, memory, disk, and top process stats for the local machine.
    """
    try:
        import psutil

        processes = []
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = proc.info
                processes.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu": round(info.get("cpu_percent") or 0, 2),
                    "memory": round(info.get("memory_percent") or 0, 2),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        top = sorted(
            processes, key=lambda item: (item["cpu"], item["memory"]), reverse=True
        )[:8]

        disk = psutil.disk_usage("/")
        stats = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "memory_used_gb": round(psutil.virtual_memory().used / 1024**3, 2),
            "memory_total_gb": round(psutil.virtual_memory().total / 1024**3, 2),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / 1024**3, 1),
            "disk_total_gb": round(disk.total / 1024**3, 1),
            "top_processes": top,
        }
        return str(stats)
    except ImportError:
        return "psutil not installed. Run: pip install psutil"
    except Exception as exc:
        return f"get_system_stats failed: {exc}"
