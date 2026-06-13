import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../targets/pybug-demo"))

from mathx import running_max


def test_repro():
    # Bug: running_max([]) raises IndexError (indexes nums[0] before loop guard).
    # On buggy code this test errors (IndexError propagates); on fixed code it passes.
    assert running_max([]) == []
