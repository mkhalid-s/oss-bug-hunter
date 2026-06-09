import widget


def test_safe_div_by_zero_returns_zero():
    # `widget` is a src-layout package — this import only resolves after the M5
    # env-bootstrap (`uv pip install -e .`). On buggy HEAD safe_div(1, 0) raises.
    assert widget.safe_div(1, 0) == 0
