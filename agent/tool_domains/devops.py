from langchain.tools import tool


@tool
def search_logs(service: str, time_range: str) -> str:
    """Search logs for a specific service over a given time range."""
    if "checkout" in service.lower():
        return f"[ERROR] {time_range}: Connection pool exhausted in {service}."
    return f"[INFO] {time_range}: Normal operations for {service}."


@tool
def get_metrics(service: str) -> str:
    """Get CPU and Memory metrics for a service."""
    if "checkout" in service.lower():
        return f"{service} metrics: CPU 45%, Memory 98% (Spike detected)."
    return f"{service} metrics: CPU 20%, Memory 40%."
