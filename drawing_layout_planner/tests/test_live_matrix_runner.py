from __future__ import annotations

import pytest

from scripts.run_layout_g0_live_matrix import _validate_loopback_origin


@pytest.mark.parametrize(
    ("value", "expected"),
    (
        ("http://localhost:5000", "http://localhost:5000"),
        ("http://127.0.0.1:5127/", "http://127.0.0.1:5127"),
        ("http://[::1]:5127", "http://[::1]:5127"),
    ),
)
def test_live_matrix_runner_accepts_only_loopback_origins(value: str, expected: str):
    assert _validate_loopback_origin(value) == expected


@pytest.mark.parametrize(
    "value",
    (
        "https://127.0.0.1:5127",
        "http://192.0.2.10:5127",
        "http://example.com:5127",
        "http://127.0.0.1:5127/api",
        "http://127.0.0.1",
    ),
)
def test_live_matrix_runner_rejects_non_loopback_or_non_origin_urls(value: str):
    with pytest.raises(ValueError):
        _validate_loopback_origin(value)
