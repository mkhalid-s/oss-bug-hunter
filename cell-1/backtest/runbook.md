# Day 2 Backtest Runbook
Run each entry's prompt against a fresh subagent (subagent_type=general-purpose or code-reviewer). After each run, paste the agent's findings YAML into the entry's `findings.yaml`, then label each finding in `labels.yaml`. When all 10 are done, run: `python3 scripts/day2-backtest.py score`.

## Entry 1 — issue #5870
- Title: `EnumMap` and `EnumSet` properties ignore `@JsonDeserialize(contentConverter)`
- Parent commit: `575f922a7590`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5870`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5870/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5870/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5870/labels.yaml`

## Entry 2 — issue #5851
- Title: Regression of `JsonTypeInfo.Id.MINIMAL_CLASS` in the 3.x branch
- Parent commit: `8c6439de9732`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5851`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5851/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5851/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5851/labels.yaml`

## Entry 3 — issue #5840
- Title: Jackson 2.21 throws Conflicting property-based creators if both default (0-arg) and multi-arg constructor annotated
- Parent commit: `aa6ee366c4d4`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5840`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5840/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5840/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5840/labels.yaml`

## Entry 4 — issue #5819
- Title: `JsonNodeFeature.STRIP_TRAILING_BIGDECIMAL_ZEROES` not working with `ObjectMapper.valueToTree()`
- Parent commit: `bc93f2410a6f`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5819`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5819/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5819/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5819/labels.yaml`

## Entry 5 — issue #5813
- Title: `JsonMapper` not thread-safe when using custom serializers
- Parent commit: `cff32da7aede`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5813`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5813/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5813/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5813/labels.yaml`

## Entry 6 — issue #5734
- Title: `DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES` treats absent field same as explicit `null`
- Parent commit: `cfe49187fbdd`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5734`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5734/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5734/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5734/labels.yaml`

## Entry 7 — issue #5616
- Title: `ObjectWriter` serializes reference types (like `AtomicReference`, `Optional`) with subtypes incompletely
- Parent commit: `8c8bd3dbc0ad`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5616`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5616/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5616/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5616/labels.yaml`

## Entry 8 — issue #5615
- Title: JsonMapper seems to be not thread-safe when using the polymorphic `JsonTypeInfo.As.PROPERTY` definition (and `@JsonIgnoreProperties`)
- Parent commit: `90087749d89c`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5615`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5615/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5615/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5615/labels.yaml`

## Entry 9 — issue #5608
- Title: Confusing error-handling logic in `FunctionalScalarDeserializer` (Wrapped with exceptions)
- Parent commit: `366f3ca05a35`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5608`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5608/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5608/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5608/labels.yaml`

## Entry 10 — issue #5978
- Title: BuilderBasedDeserializer unwrapped update path still uses ignorable-only check
- Parent commit: `4e70420b31c4`
- Worktree: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/worktrees/5978`
- Prompt: `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5978/prompt.md`
- After run: paste findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5978/findings.yaml`
- After review: label findings → `/workspaces/OpenSource/oss-bug-hunter/cell-1/backtest/runs/5978/labels.yaml`

