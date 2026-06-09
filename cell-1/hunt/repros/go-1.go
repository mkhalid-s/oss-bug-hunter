package gobug

import "testing"

func TestRunningMaxEmpty(t *testing.T) {
	got := RunningMax([]int{})
	if len(got) != 0 {
		t.Fatalf("want empty, got %v", got)
	}
}
