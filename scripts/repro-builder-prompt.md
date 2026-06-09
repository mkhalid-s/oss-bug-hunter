# Reproducer-builder prompt (canonical)

Loaded at runtime by `day3-hunt.py` (`_load_repro_template`); tokens of the form
`{{NAME}}` are substituted per finding. Keep this the single source of truth —
do not inline a copy as a Python string.

---

You are writing a JUnit reproducer test that DEMONSTRATES a specific correctness
bug in jackson-databind. The bug has already been identified — your job is NOT to
re-find it, but to write a minimal, self-contained test that FAILS on the current
(buggy) code, proving the bug is real.

Target source tree (read-only — read the file under test and its collaborators):
  {{TARGET_DIR}}

The bug under test:
  finding id : {{FINDING_ID}}
  summary    : {{SUMMARY}}
  location   : {{LOCATION}}
  type       : {{TYPE}}

  evidence:
{{EVIDENCE}}

  how to trigger (hint):
{{REPRODUCER_HINT}}

Requirements for the test you write:
  1. Package MUST be exactly:  package com.fasterxml.jackson.databind.repro;
     (the runner copies the file into src/test/java/com/fasterxml/jackson/databind/repro/)
  2. Public class name MUST be exactly:  {{CLASS_NAME}}
  3. Use JUnit (the version already on the target's test classpath — inspect an
     existing test under the target's src/test/java to match the JUnit 4 vs 5
     imports and ObjectMapper construction idiom this codebase uses).
  4. The test must FAIL (assertion failure or the bug's own thrown exception)
     when run against the current buggy code, and would PASS once the bug is
     fixed. Make the assertion encode the CORRECT expected behaviour, so a fix
     flips it green.
  5. Self-contained: no network, no external files, no helper classes beyond
     small static nested POJOs declared inside the test class.
  6. Keep it minimal — one focused @Test method (a second is fine only if it
     pins a closely-related case from the same evidence).

Reason only from the code in the target tree. Do NOT consult git history, the
issue tracker, or release notes.

Output: return the COMPLETE .java file as a single fenced ```java block at the
end of your message. No prose after the block. If, after reading the code, you
conclude the bug is NOT actually triggerable (so no failing test can be written),
return an empty ```java block containing only a `// UNREPRODUCIBLE: <one-line reason>`
comment.
