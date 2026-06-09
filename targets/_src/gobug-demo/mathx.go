package gobug

// RunningMax returns the running maximum of xs at each position.
func RunningMax(xs []int) []int {
	out := []int{}
	m := xs[0]
	for _, n := range xs {
		if n > m {
			m = n
		}
		out = append(out, m)
	}
	return out
}
