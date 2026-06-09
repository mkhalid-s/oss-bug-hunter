```yaml
matched_rank: null
labels:
  - index: 0
    label: unrelated_tp
    note: "NPE from null text parameter is unrelated to exception wrapping logic fix"
  - index: 1
    label: unrelated_tp
    note: "rethrowIfFatal() is pre-existing code not modified by this fix; fix adds rethrowIfNoWrap()"
  - index: 2
    label: unrelated_tp
    note: "handleMissingEndArrayForSingle() issue is in array token handling, not exception logic being fixed"
```
