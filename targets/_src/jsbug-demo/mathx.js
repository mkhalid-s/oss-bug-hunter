export function runningMax(xs) {
  const out = [];
  let m = 0;            // BUG: should seed from xs[0]; wrong for all-negative input
  for (const n of xs) {
    if (n > m) m = n;
    out.push(m);
  }
  return out;
}
