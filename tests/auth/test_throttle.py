from app.security.session import safe_next_path
from app.security.throttle import LoginThrottle


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _throttle(clock: Clock) -> LoginThrottle:
    return LoginThrottle(window_seconds=60, pair_limit=3, ip_limit=5, username_limit=8, clock=clock)


def test_blocks_after_pair_limit_and_recovers_after_window() -> None:
    clock = Clock()
    throttle = _throttle(clock)
    for _ in range(3):
        assert throttle.retry_after("1.1.1.1", "alice") == 0
        throttle.record_failure("1.1.1.1", "alice")

    assert 0 < throttle.retry_after("1.1.1.1", "alice") <= 60
    clock.now += 61
    assert throttle.retry_after("1.1.1.1", "alice") == 0


def test_other_ip_is_not_locked_out_for_same_username() -> None:
    throttle = _throttle(Clock())
    for _ in range(3):
        throttle.record_failure("1.1.1.1", "alice")

    assert throttle.retry_after("1.1.1.1", "alice") > 0
    assert throttle.retry_after("2.2.2.2", "alice") == 0


def test_ip_limit_applies_across_usernames() -> None:
    throttle = _throttle(Clock())
    for i in range(5):
        throttle.record_failure("1.1.1.1", f"user{i}")

    assert throttle.retry_after("1.1.1.1", "someone_new") > 0


def test_username_is_case_insensitive_and_success_resets_pair() -> None:
    throttle = _throttle(Clock())
    for _ in range(3):
        throttle.record_failure("1.1.1.1", "Alice")
    assert throttle.retry_after("1.1.1.1", "alice") > 0

    throttle.reset("1.1.1.1", "alice")
    assert throttle.retry_after("1.1.1.1", "alice") == 0


def test_safe_next_path_rejects_external_targets() -> None:
    assert safe_next_path("/account") == "/account"
    for bad in ["//evil.com", "https://evil.com", "evil.com", "/\\evil.com", "/a\r\nb", None, ""]:
        assert safe_next_path(bad) == "/account"
