/// Running maximum at each position.
pub fn running_max(xs: &[i32]) -> Vec<i32> {
    let mut out = Vec::new();
    let mut m = xs[0];
    for &n in xs {
        if n > m {
            m = n;
        }
        out.push(m);
    }
    out
}
