from api.rate_limit import check_rate_limit, headers_for, reset_rate_limits


def test_check_rate_limit_allows_then_blocks_until_window_expires():
    reset_rate_limits()

    first = check_rate_limit("unit", "actor", limit=2, window_seconds=10, now=100.0)
    second = check_rate_limit("unit", "actor", limit=2, window_seconds=10, now=101.0)
    third = check_rate_limit("unit", "actor", limit=2, window_seconds=10, now=102.0)
    after_window = check_rate_limit("unit", "actor", limit=2, window_seconds=10, now=111.0)

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert third.allowed is False
    assert third.retry_after_seconds == 8
    assert headers_for(third)["Retry-After"] == "8"
    assert after_window.allowed is True


def test_rate_limit_scopes_are_independent():
    reset_rate_limits()

    assert check_rate_limit("one", "actor", limit=1, window_seconds=10, now=1.0).allowed is True
    assert check_rate_limit("two", "actor", limit=1, window_seconds=10, now=1.0).allowed is True
    assert check_rate_limit("one", "actor", limit=1, window_seconds=10, now=2.0).allowed is False
