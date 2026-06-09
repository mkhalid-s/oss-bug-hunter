package com.fasterxml.jackson.databind.repro;

import java.util.Set;
import java.util.TreeSet;

import org.junit.jupiter.api.Test;

import com.fasterxml.jackson.annotation.JsonIdentityInfo;
import com.fasterxml.jackson.annotation.ObjectIdGenerators;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.exc.MismatchedInputException;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import static com.fasterxml.jackson.databind.testutil.DatabindTestUtil.newJsonMapper;

/**
 * Reproduces finding ec-1:
 *
 * CollectionDeserializer._deserializeWithObjectId (the Object-Id collection path)
 * adds a leftover JSON {@code null} directly to the backing collection via
 * CollectionReferringAccumulator.add(...) -> _result.add(null), bypassing the
 * {@code _tryToAddNull(...)} NPE-guard that every sibling element path uses.
 *
 * For a null-hostile collection (TreeSet) whose elements carry an Object Id
 * (@JsonIdentityInfo), an input that mixes id-bearing objects with a JSON null
 * must produce the same friendly MismatchedInputException ("does not accept
 * `null` values") that the non-ObjectId path produces (see
 * JDKCollectionsDeserTest#testNullsWithTreeSet). On the buggy code the raw
 * NullPointerException from TreeSet.add(null) escapes the guard and is wrapped
 * as a generic mapping failure instead, so this assertion fails. A fix that
 * routes the null through _tryToAddNull flips it green.
 */
public class Repro_ec_1
{
    @JsonIdentityInfo(generator = ObjectIdGenerators.IntSequenceGenerator.class, property = "@id")
    static class Node implements Comparable<Node> {
        public String v;

        @Override
        public int compareTo(Node o) {
            // Non-null-safe ordering; TreeSet must reject any null element
            // before this is ever invoked.
            return this.v.compareTo(o.v);
        }
    }

    private final ObjectMapper MAPPER = newJsonMapper();

    @Test
    public void objectIdCollectionNullIntoNullHostileSetIsReported() throws Exception
    {
        // First element bears an Object Id -> _deserializeWithObjectId runs;
        // the trailing JSON null is the element under test.
        String json = "[{\"@id\":1,\"v\":\"a\"}, null]";

        try {
            Set<Node> result = MAPPER.readValue(json, new TypeReference<TreeSet<Node>>() { });
            fail("Should not pass: TreeSet must reject null element, got " + result);
        } catch (MismatchedInputException e) {
            // Correct, fixed behaviour: friendly, guarded report.
            String msg = String.valueOf(e.getMessage());
            assertTrue(msg.contains("does not accept `null` values"),
                    "Expected friendly null-rejection message but got: " + msg);
        }
    }
}
