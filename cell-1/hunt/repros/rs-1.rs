#[test]
fn running_max_empty_returns_empty() {
    let got = rustbug::running_max(&[]);
    assert!(got.is_empty(), "want empty, got {:?}", got);
}
