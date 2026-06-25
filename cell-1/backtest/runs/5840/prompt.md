You are reviewing a code module for CORRECTNESS bugs (not security, not style).

Working directory (already checked out at the relevant snapshot):
  /workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5840

Files to investigate (read these in full, plus any callers/callees needed):
  - src/main/java/com/fasterxml/jackson/databind/introspect/POJOPropertiesCollector.java

Task: find at most 5 distinct correctness bugs in the listed files. Examples
of correctness bugs: NullPointerException on unusual input, off-by-one, wrong
return value, edge case in encoding/timezone/format handling, race condition,
infinite loop, deadlock, incorrect generic-type handling, dropped exception.

Out of scope: security (deserialization gadgets, authn/authz), code style,
refactoring opportunities, performance unless it's a correctness issue (e.g.,
StackOverflow), missing tests, javadoc gaps.

Confidence bar: only report findings where you would bet money that triggering
the described input causes the described wrong behavior. If unsure, OMIT.

Return your findings as a single YAML block at the end of your message. If you
find nothing, return an empty list.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<file:line-range>"
    type: "<NPE | off-by-one | wrong-return | edge-case | race | other>"
    evidence: "<excerpt of buggy code + why it's wrong>"
    reproducer_hint: "<one-line: how to trigger>"
```

Do NOT consult the git history, release notes, or issue tracker. Reason only
from the code in the working directory above.
