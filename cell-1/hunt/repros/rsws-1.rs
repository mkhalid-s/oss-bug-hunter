//! Workspace reproducer for the `mathx` member (placed in mathx/tests/ by the adapter, #51).
use mathx::running_max;

#[test]
fn running_max_empty_slice_must_not_panic() {
    // BUG: running_max indexes [0] without an emptiness check → panics on &[].
    assert_eq!(running_max(&[]), Vec::<i64>::new());
}
