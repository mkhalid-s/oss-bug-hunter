"""A tiny src-layout package — importable only after `pip install -e .`
(that is what makes M5 env-bootstrap load-bearing for this target)."""


def safe_div(a, b):
    """Divide a by b; should return 0 when b == 0 (the contract)."""
    return a / b        # BUG: no zero guard -> ZeroDivisionError
