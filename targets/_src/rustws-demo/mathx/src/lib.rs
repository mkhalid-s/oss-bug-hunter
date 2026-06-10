/// Running maximum at each position.
pub fn running_max(xs: &[i64]) -> Vec<i64> {
    let mut out = Vec::new();
    let mut m = xs[0];          // BUG: panics on an empty slice (no emptiness check)
    for &n in xs {
        if n > m {
            m = n;
        }
        out.push(m);
    }
    out
}
