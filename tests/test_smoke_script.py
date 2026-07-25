from scripts import smoke_check


def test_smoke_check_summary_fails_when_api_unreachable(monkeypatch, capsys):
    def raise_unreachable(*args, **kwargs):
        raise OSError("api unreachable")

    monkeypatch.setattr(smoke_check, "request_json", raise_unreachable)

    exit_code = smoke_check.main()

    assert exit_code == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output
    assert "api unreachable" in output


def test_smoke_check_summary_passes_with_healthy_api(monkeypatch, capsys):
    calls = []

    def fake_request_json(path, method="GET", body=None, token=None):
        calls.append({"path": path, "method": method, "body": body, "token": token})
        if path == "/token":
            return {"access_token": "test-token", "token_type": "bearer"}
        if path == "/investigate":
            assert token == "test-token"
            return {"job_id": "job-1", "status": "queued"}
        return {"ok": True}

    monkeypatch.setattr(smoke_check, "request_json", fake_request_json)

    exit_code = smoke_check.main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert calls[-1]["path"] == "/investigate"
    assert calls[-1]["body"] == {"goal": "Run a smoke check: report OK only."}
