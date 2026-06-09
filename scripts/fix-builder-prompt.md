# Fix-builder prompt (canonical)

Loaded at runtime by `day3-hunt.py` (`_load_fix_template`); `{{NAME}}` tokens are
substituted per finding. Keep this the single source of truth — do not inline a
copy as a Python string.

---

You are proposing a MINIMAL source fix for a confirmed correctness bug in
jackson-databind. A JUnit reproducer test already demonstrates the bug (it FAILS
on the current code). Your job: produce a patch that makes that reproducer PASS,
without breaking other behaviour.

Target source tree (read the file under test and its collaborators):
  {{TARGET_DIR}}

The bug:
  finding id : {{FINDING_ID}}
  summary    : {{SUMMARY}}
  location   : {{LOCATION}}
  type       : {{TYPE}}

  evidence:
{{EVIDENCE}}

  how to trigger (hint):
{{REPRODUCER_HINT}}

The reproducer test that must go from FAIL → PASS ({{REPRO_FQCN}}):

```java
{{REPRO_SOURCE}}
```

Requirements for your patch:
  1. Fix the ROOT CAUSE in the main source (typically the file in `location`),
     not the symptom and NOT the test. Do not edit any test file.
  2. Minimal and targeted — the smallest change that makes the reproducer pass.
     Match the surrounding code's style, error-handling idioms, and null-handling
     conventions (read sibling methods to mirror them).
  3. Must compile and preserve existing behaviour for all other inputs — do not
     regress the non-bug paths. Prefer the same mechanism the codebase already
     uses elsewhere for this situation (the evidence often names a sibling path
     that handles it correctly).
  4. Reason only from the code in the target tree. Do NOT consult git history,
     the issue tracker, or release notes.

Output: return the COMPLETE fix as a single unified-diff in one fenced ```diff
block at the end of your message, with git-style paths so `git apply` works:

```diff
diff --git a/src/main/java/.../Foo.java b/src/main/java/.../Foo.java
--- a/src/main/java/.../Foo.java
+++ b/src/main/java/.../Foo.java
@@ -120,7 +120,7 @@
-        old line
+        new line
```

No prose after the block. If, after reading the code, you conclude no minimal
correct fix is possible, return an empty ```diff block containing only a
`# NO-FIX: <one-line reason>` comment.
