You are writing a JUnit reproducer test that DEMONSTRATES a specific correctness
bug in jackson-databind. The bug has already been identified — your job is NOT to
re-find it, but to write a minimal, self-contained test that FAILS on the current
(buggy) code, proving the bug is real.

Target source tree (read-only — read the file under test and its collaborators):
  /workspaces/OpenSource/oss-bug-hunter/targets/jackson-databind

The bug under test:
  finding id : ec-1
  summary    : Object-Id collection path adds JSON null directly to a null-hostile collection, bypassing the _tryToAddNull NPE-guard used by all sibling paths
  location   : src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java:464-479
  type       : empty-collection

  evidence:
    In _deserializeWithObjectId, a VALUE_NULL element resolves to:
        if (value == null) {
            value = _nullProvider.getNullValue(ctxt);
            if (value == null && _skipNullValues) {
                continue;
            }
        }
        referringAccumulator.add(value);   // value may be null here
    When _skipNullValues is false and the null provider yields null, the null
    is passed to CollectionReferringAccumulator.add(), which does
    `_result.add(value)` (line 594) -> a raw add(null). Every other element path
    in this class (_deserializeFromArray line 366, handleNonArray line 430,
    _wrapSingleWithObjectId line 517) routes a leftover null through
    `_tryToAddNull(...)`, which catches the NullPointerException thrown by
    null-hostile collections (e.g. TreeSet) and reports a friendly
    handleUnexpectedToken message instead. The Object-Id path omits this guard,
    so the same input throws an unhandled/raw NPE only when the collection
    elements carry Object Ids.

  how to trigger (hint):
    // Set<Node> where Node has @JsonIdentityInfo, backing collection is a TreeSet
    // (which rejects null). Input contains a null element among id-bearing objects:
    String json = "[{\"@id\":1,\"v\":\"a\"}, null]";
    mapper.readValue(json, new TypeReference<TreeSet<Node>>(){});
    // Plain (non-ObjectId) elements would yield a clean InvalidFormat/handleUnexpectedToken;
    // with @JsonIdentityInfo present, _deserializeWithObjectId runs and add(null) throws raw NPE.

Requirements for the test you write:
  1. Package MUST be exactly:  package com.fasterxml.jackson.databind.repro;
     (the runner copies the file into src/test/java/com/fasterxml/jackson/databind/repro/)
  2. Public class name MUST be exactly:  Repro_ec_1
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
