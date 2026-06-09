from mathx import running_max


def test_running_max_empty_returns_empty():
    assert running_max([]) == []
