You are reviewing Java code in the `jackson-databind` project for CORRECTNESS
bugs from an edge-case angle. This is a novel-hunt pass: you do not know
which (if any) of these files contains a bug.

Working directory: /workspaces/OpenSource/oss-bug-hunter/targets/jackson-databind

Files to review (read each in full, plus callers/callees as needed):
  - src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializer.java
  - src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializerBase.java
  - src/main/java/com/fasterxml/jackson/databind/deser/std/NumberDeserializers.java
  - src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java
  - src/main/java/com/fasterxml/jackson/databind/deser/impl/PropertyValueBuffer.java

# Find at most 5 distinct edge-case correctness bugs in the listed files

Edge-case categories to consider (use as a checklist — not all apply to every file):
- Timezone / DST: parsing/serializing dates around DST transitions, fixed
  offsets vs zone IDs, ZoneOffset.UTC vs ZoneId.of("UTC") equivalence
- Unicode: surrogate pairs, combining characters, BOM in input streams,
  non-BMP code points in string parsing
- Integer boundaries: MAX_VALUE+1 overflow, MIN_VALUE negation, unsigned-vs-signed
  in size/length math, overflow in size hint allocation
- Empty / singleton / deeply nested collections: empty arrays/maps where
  iteration assumed non-empty, single-element optimizations that break for size 0
- Locale-dependent formatting: Locale.getDefault() leaking into "should be
  invariant" code paths (decimal separator, case folding)
- Polymorphic-type resolution: subtype not yet loaded when parent deserializer
  runs, type id collision between equally-named classes in different packages
- Concurrent cache access: deserializer-cache reads during invalidation,
  double-construction of cached deserializers
- Recursive structures: cycles in object graphs, deep recursion hitting
  StackOverflowError before MAX_VALUE depth

# Out of scope (do not report)

- Security (deserialization gadgets, authn/authz, crypto)
- Code style, naming, formatting
- Refactoring opportunities, dead code, redundant abstractions
- Performance unless it's a correctness issue (StackOverflow, infinite loop)
- Generic NPE / off-by-one without an edge-case trigger (those are the
  code-quality angle's territory)

# Confidence bar

Only report findings where you would bet money that the described edge-case
input causes the described wrong behavior. If unsure, OMIT.

# Constraints

- Do NOT consult: git history, release notes, issue tracker, OSV/GHSA, the
  internet. Reason only from the source code in the working directory.
- Each finding must cite a specific file:line-range.
- Each finding must be derivable from the code shown in `evidence` alone.
- Each `reproducer_hint` must be a concrete Java snippet exhibiting the
  edge case (specific bad input value, specific call sequence).

# Output format

Return a single YAML block at the end of your message. If you find nothing,
return `findings: []`.

```yaml
findings:
  - summary: "<one-line description>"
    location: "<repo-relative-path:line-range>"
    type: "<timezone | unicode | integer-overflow | empty-collection | locale | polymorphic | concurrent-cache | recursion | other>"
    evidence: |
      <buggy code excerpt + 1-2 sentences on the edge case>
    reproducer_hint: |
      <Java snippet, 1-5 lines, with the specific edge-case input>
```