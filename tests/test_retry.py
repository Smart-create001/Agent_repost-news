from src.retry import retry_delay


def test_retry_delay_bounded_and_does_not_ignore_invalid_provider_values():
    assert retry_delay(0, {"Retry-After": "12"}) == 12
    assert retry_delay(0, {"Retry-After": "31"}) is None
    assert retry_delay(0, {"Retry-After": "invalid"}) is None
    assert retry_delay(2) == 4
