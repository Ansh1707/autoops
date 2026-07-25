from agent.observability import build_event


def test_build_event_redacts_sensitive_fields():
    event = build_event(
        "tool_call",
        job_id="job-1",
        args={
            "Authorization": "Bearer secret",
            "nested": {"gmail_token": "secret-token"},
            "safe": "visible",
        },
    )

    assert event["event"] == "tool_call"
    assert event["job_id"] == "job-1"
    assert event["args"]["Authorization"] == "[REDACTED]"
    assert event["args"]["nested"]["gmail_token"] == "[REDACTED]"
    assert event["args"]["safe"] == "visible"
    assert "timestamp" in event
