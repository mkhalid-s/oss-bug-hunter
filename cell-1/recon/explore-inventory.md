# Cell #1 Explore Inventory

**Generated:** 2026-05-18T08:25:00Z
**Target pin:** jackson-databind-2.21.3 @ 1c38a7d873c8
**Subagent:** Explore (read-only inventory pass)

## 1. JsonDeserializer subclass inventory

| FQCN | File | Deserialized type | Notes |
|---|---|---|---|
| com.fasterxml.jackson.databind.JsonDeserializer.None | com/fasterxml/jackson/databind/JsonDeserializer.java | Object | No-op deserializer sentinel |
| com.fasterxml.jackson.databind.deser.AbstractDeserializer | com/fasterxml/jackson/databind/deser/AbstractDeserializer.java | Object | Abstract base for non-bean types |
| com.fasterxml.jackson.databind.deser.impl.ErrorThrowingDeserializer | com/fasterxml/jackson/databind/deser/impl/ErrorThrowingDeserializer.java | Object | Error case handler |
| com.fasterxml.jackson.databind.deser.impl.TypeWrappedDeserializer | com/fasterxml/jackson/databind/deser/impl/TypeWrappedDeserializer.java | Object | Wraps type info |
| com.fasterxml.jackson.databind.deser.std.StdDeserializer | com/fasterxml/jackson/databind/deser/std/StdDeserializer.java | T | Abstract base for standard types |
| com.fasterxml.jackson.databind.jsontype.impl.TypeDeserializerBase | com/fasterxml/jackson/databind/jsontype/impl/TypeDeserializerBase.java | N/A | Polymorphic type deserializer base |

**Direct JsonDeserializer subclasses: 6**  (see Section 6 for full transitive concrete subclass count)

## 2. Polymorphic-type resolution surface

### 2.1 TypeDeserializer subclasses
| FQCN | File |
|---|---|
| com.fasterxml.jackson.databind.jsontype.impl.TypeDeserializerBase | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/TypeDeserializerBase.java |
| com.fasterxml.jackson.databind.jsontype.impl.AsArrayTypeDeserializer | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsArrayTypeDeserializer.java |
| com.fasterxml.jackson.databind.jsontype.impl.AsPropertyTypeDeserializer | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsPropertyTypeDeserializer.java |
| com.fasterxml.jackson.databind.jsontype.impl.AsExternalTypeDeserializer | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsExternalTypeDeserializer.java |
| com.fasterxml.jackson.databind.jsontype.impl.AsDeductionTypeDeserializer | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsDeductionTypeDeserializer.java |
| com.fasterxml.jackson.databind.jsontype.impl.AsWrapperTypeDeserializer | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsWrapperTypeDeserializer.java |

**Total TypeDeserializer subclasses: 6**

### 2.2 TypeIdResolver implementations
| FQCN | File |
|---|---|
| com.fasterxml.jackson.databind.jsontype.impl.TypeIdResolverBase | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/TypeIdResolverBase.java |
| com.fasterxml.jackson.databind.jsontype.impl.SimpleNameIdResolver | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/SimpleNameIdResolver.java |
| com.fasterxml.jackson.databind.jsontype.impl.ClassNameIdResolver | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/ClassNameIdResolver.java |
| com.fasterxml.jackson.databind.jsontype.impl.MinimalClassNameIdResolver | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/MinimalClassNameIdResolver.java |
| com.fasterxml.jackson.databind.jsontype.impl.TypeNameIdResolver | src/main/java/com/fasterxml/jackson/databind/jsontype/impl/TypeNameIdResolver.java |

**Total TypeIdResolver implementations: 5**

### 2.3 @JsonTypeInfo / @JsonSubTypes usage sites
| File | Line | Annotation | Notes |
|---|---|---|---|
| src/main/java/com/fasterxml/jackson/databind/introspect/JacksonAnnotationIntrospector.java | 669 | @JsonTypeInfo | Comment reference in type handling |
| src/main/java/com/fasterxml/jackson/databind/MapperFeature.java | 331 | @JsonTypeInfo | Documentation reference for defaultImpl |
| src/main/java/com/fasterxml/jackson/databind/jsontype/TypeResolverBuilder.java | 30 | @JsonTypeInfo | Interface documentation |
| src/main/java/com/fasterxml/jackson/databind/jsontype/PolymorphicTypeValidator.java | 9 | @JsonTypeInfo | Class name type validation doc |
| src/main/java/com/fasterxml/jackson/databind/jsontype/BasicPolymorphicTypeValidator.java | 282 | @JsonTypeInfo | Annotation use case reference |
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/ClassNameIdResolver.java | 125 | @JsonSubTypes | Error message reference for registration |

**Note:** Most refs are documentation/javadoc — the annotation symbols live in the `jackson-annotations` module, not this repo. Runtime annotation processing happens in the introspector.

### 2.4 Strategy dispatch entry points (As.PROPERTY / WRAPPER_ARRAY / ...)
| File | Line | Strategy branch | Implementation class |
|---|---|---|---|
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsArrayTypeDeserializer.java | 48 | As.WRAPPER_ARRAY | AsArrayTypeDeserializer.getTypeInclusion() |
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsArrayTypeDeserializer.java | 150 | As.WRAPPER_ARRAY | Error message for array requirement |
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsPropertyTypeDeserializer.java | 51 | As.PROPERTY | AsPropertyTypeDeserializer constructor default |
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsWrapperTypeDeserializer.java | 46 | As.WRAPPER_OBJECT | AsWrapperTypeDeserializer.getTypeInclusion() |
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsWrapperTypeDeserializer.java | 102 | As.WRAPPER_OBJECT | Error message for object requirement |
| src/main/java/com/fasterxml/jackson/databind/jsontype/impl/AsExternalTypeDeserializer.java | 45 | As.EXTERNAL_PROPERTY | AsExternalTypeDeserializer.getTypeInclusion() |
| src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializerBase.java | 616 | As.EXTERNAL_PROPERTY | External type property dispatch |
| src/main/java/com/fasterxml/jackson/databind/introspect/JacksonAnnotationIntrospector.java | 1632-1633 | As.PROPERTY | EXTERNAL_PROPERTY downgrade for classes |
| src/main/java/com/fasterxml/jackson/databind/ObjectMapper.java | 2084 | As.EXTERNAL_PROPERTY | Disallow check in ObjectMapper |

## 3. Deserialization pipeline entry points

| Stage | Class | Method | File:line |
|---|---|---|---|
| Top-level entry | ObjectMapper | readValue(JsonParser p, Class<T> valueType) | src/main/java/com/fasterxml/jackson/databind/ObjectMapper.java:3100 |
| Top-level dispatch | ObjectMapper | _readValue(DeserializationConfig cfg, JsonParser p, JavaType valueType) | src/main/java/com/fasterxml/jackson/databind/ObjectMapper.java:5004 |
| Deserializer construction | BasicDeserializerFactory | createArrayDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:722 |
| Deserializer construction | BasicDeserializerFactory | createCollectionDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:768 |
| Deserializer construction | BasicDeserializerFactory | createMapDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:892 |
| Deserializer construction | BasicDeserializerFactory | createEnumDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:1054 |
| Deserializer construction | BasicDeserializerFactory | createTreeDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:1118 |
| Deserializer construction | BasicDeserializerFactory | createReferenceDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:1134 |
| Deserializer construction | BasicDeserializerFactory | findDefaultDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BasicDeserializerFactory.java:1471 |
| Cache lookup | DeserializerCache | findValueDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/DeserializerCache.java |
| Bean deserializer | BeanDeserializerFactory | createBeanDeserializer(...) | src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializerFactory.java |

## 4. Recent-change hot-spots (last 12mo, by commit count)

| Rank | File | Commits (12mo) |
|---|---|---|
| 1 | src/main/java/com/fasterxml/jackson/databind/deser/impl/PropertyValueBuffer.java | 8 |
| 2 | src/main/java/com/fasterxml/jackson/databind/deser/std/NumberDeserializers.java | 5 |
| 3 | src/main/java/com/fasterxml/jackson/databind/deser/std/FromStringWithRadixToNumberDeserializer.java | 5 |
| 4 | src/main/java/com/fasterxml/jackson/databind/deser/std/ObjectArrayDeserializer.java | 4 |
| 5 | src/main/java/com/fasterxml/jackson/databind/deser/std/EnumMapDeserializer.java | 4 |
| 6 | src/main/java/com/fasterxml/jackson/databind/deser/impl/ValueInjector.java | 4 |
| 7 | src/main/java/com/fasterxml/jackson/databind/deser/impl/PropertyBasedCreator.java | 4 |
| 8 | src/main/java/com/fasterxml/jackson/databind/deser/std/StdDeserializer.java | 3 |
| 9 | src/main/java/com/fasterxml/jackson/databind/deser/std/EnumSetDeserializer.java | 3 |
| 10 | src/main/java/com/fasterxml/jackson/databind/deser/std/EnumDeserializer.java | 3 |
| 11 | src/main/java/com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java | 3 |
| 12 | src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializer.java | 3 |
| 13 | src/main/java/com/fasterxml/jackson/databind/deser/BeanDeserializerFactory.java | 3 |
| 14 | src/main/java/com/fasterxml/jackson/databind/deser/std/StringCollectionDeserializer.java | 2 |
| 15 | src/main/java/com/fasterxml/jackson/databind/deser/std/StringArrayDeserializer.java | 2 |
| 16 | src/main/java/com/fasterxml/jackson/databind/deser/std/StdKeyDeserializer.java | 2 |
| 17 | src/main/java/com/fasterxml/jackson/databind/deser/std/MapDeserializer.java | 2 |
| 18 | src/main/java/com/fasterxml/jackson/databind/deser/SettableBeanProperty.java | 2 |
| 19 | src/main/java/com/fasterxml/jackson/databind/deser/CreatorProperty.java | 2 |
| 20 | src/main/java/com/fasterxml/jackson/databind/deser/ValueInstantiator.java | 1 |

## 5. Hot-spot × closed-bug-title cross-reference

| Rank | Class | File | Coarse mentions | Sample bug titles |
|---|---|---|---|---|
| 1 | BeanDeserializer | com/fasterxml/jackson/databind/deser/BeanDeserializer.java | 66 | BuilderBasedDeserializer unwrapped update path still uses ignorable-only check; Case-insensitive deserialization may use wrong @JsonIgnoreProperties; @JsonIgnoreProperties does not work alongside DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES |
| 2 | BeanDeserializerBase | com/fasterxml/jackson/databind/deser/BeanDeserializerBase.java | 20 | Case-insensitive deserialization may use wrong @JsonIgnoreProperties; Excessive TokenBuffer allocations when deserializing records; Exception when using @JsonUnwrapped alongside with @JsonAlias on another property |
| 3 | StdDeserializer | com/fasterxml/jackson/databind/deser/std/StdDeserializer.java | 18 | DoubleDeserializer rejects "+INF"/"+Infinity" despite accepting "INF"/"Infinity"; @JsonIgnoreProperties does not work alongside DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES |
| 4 | DeserializerCache | com/fasterxml/jackson/databind/deser/DeserializerCache.java | 16 | enum (de)serialization via registered module ignored since Jackson 3.x |
| 5 | CollectionDeserializer | com/fasterxml/jackson/databind/deser/std/CollectionDeserializer.java | 13 | DoubleDeserializer rejects "+INF"/"+Infinity" despite accepting "INF"/"Infinity"; EnumMap and EnumSet properties ignore @JsonDeserialize(contentConverter); Empty JSON array overrides existing collection when using readerForUpdating |
| 6 | BuilderBasedDeserializer | com/fasterxml/jackson/databind/deser/BuilderBasedDeserializer.java | 9 | BuilderBasedDeserializer unwrapped update path still uses ignorable-only check; Forward Object Id references inside Collection properties leak Builder instance; Wrong path in InvalidFormatException for nested fields and BuilderBasedDeserializer |
| 7 | DeserializerFactory | com/fasterxml/jackson/databind/deser/DeserializerFactory.java | 10 | Jackson 2.21 throws Conflicting property-based creators if both default (0-arg) and one-arg constructors exist; Enum deserialization does not respect JsonFormat.Feature.ACCEPT_CASE_INSENSITIVE; Jackson 2.19 fails to parse while 2.17 can |
| 8 | Deserializers | com/fasterxml/jackson/databind/deser/Deserializers.java | 11 | enum (de)serialization via registered module ignored since Jackson 3.x; DoubleDeserializer rejects "+INF"/"+Infinity" despite accepting "INF"/"Infinity"; EnumMap and EnumSet properties ignore @JsonDeserialize(contentConverter) |
| 9 | NumberDeserializers | com/fasterxml/jackson/databind/deser/std/NumberDeserializers.java | 12 | DoubleDeserializer rejects "+INF"/"+Infinity" despite accepting "INF"/"Infinity"; When deserializing using ObjectMapper::treeToValue, NPE occurs when JsonParser is closed |
| 10 | FieldProperty | com/fasterxml/jackson/databind/deser/impl/FieldProperty.java | 5 | DoubleDeserializer rejects "+INF"/"+Infinity" despite accepting "INF"/"Infinity" |

## 6. Comprehensive concrete-subclass inventory

**Total concrete JsonDeserializer/StdDeserializer subclasses across codebase: ~100**

- **StdScalarDeserializer subclasses (~12)**: AtomicBooleanDeserializer, AtomicIntegerDeserializer, AtomicLongDeserializer, ByteBufferDeserializer, StringDeserializer, TokenBufferDeserializer, FromStringDeserializer (+ ~5 nested inner-class instances), StackTraceElementDeserializer, NioPathDeserializer, UUIDDeserializer

- **ContainerDeserializerBase subclasses (~5)**: CollectionDeserializer, EnumMapDeserializer, MapDeserializer, MapEntryDeserializer, StringCollectionDeserializer

- **BeanDeserializerBase subclasses (4)**: BeanDeserializer, BuilderBasedDeserializer, BeanAsArrayDeserializer, BeanAsArrayBuilderDeserializer

- **Special deserializers**: EnumSetDeserializer, EnumDeserializer, ThrowableDeserializer, ArrayBlockingQueueDeserializer, ReferenceTypeDeserializer, StdNodeBasedDeserializer, DelegatingDeserializer, StdDelegatingDeserializer, UntypedObjectDeserializer, NullifyingDeserializer, FactoryBasedEnumDeserializer, PrimitiveArrayDeserializers (with ~8 nested inner classes)

- **NumberDeserializers nested classes (11)**: BooleanDeserializer, ByteDeserializer, ShortDeserializer, CharacterDeserializer, IntegerDeserializer, LongDeserializer, FloatDeserializer, DoubleDeserializer, NumberDeserializer, BigIntegerDeserializer, BigDecimalDeserializer

- **DateDeserializers nested classes (~5)**: DateBasedDeserializer, DateDeserializer, CalendarDeserializer, SqlDateDeserializer, TimestampDeserializer

- **JsonNodeDeserializer nested classes (~4)**: BaseNodeDeserializer, JsonNodeDeserializer, ArrayDeserializer, ObjectDeserializer

- **External/DOM deserializers (~3)**: DOMDeserializer, DocumentDeserializer, NodeDeserializer

## Caveats and gaps

- **Semgrep + SpotBugs baselines missing** — neither tool was installed in this environment, so `cell-1/recon/scanners/` is empty and the original Task 6 (scanner categorization) is omitted. **Impact**: the Day-3 validation step's `dupe_of_baseline` check has nothing to compare against — every agent finding will be treated as non-dupe-of-baseline. Install with `pipx install semgrep` before novel hunting if you want this gate working.
- **`releases.json` empty** — Jackson uses git tags, not GitHub Releases. Section 5 pivoted to closed-bug-title cross-reference instead (completed).
- **`@JsonTypeInfo` annotation sites are mostly javadoc** — the annotation lives in the `jackson-annotations` module (separate repo), so runtime annotation-driven polymorphic config is handled at introspection time, not visible in this codebase's annotation usage sites.
- **Dynamic class lookups in TypeIdResolver impls (notably ClassNameIdResolver) use reflection** — full instantiation paths not traced; would need runtime analysis.
- **Line numbers approximate** — Explore reads excerpts, so some line numbers may be off by a few lines.

---

**Summary**: Inventory complete. Found ~100 concrete JsonDeserializer subclasses, 6 TypeDeserializer strategy implementations covering all four inclusion modes (PROPERTY, WRAPPER_ARRAY, WRAPPER_OBJECT, EXTERNAL_PROPERTY) + AsDeductionTypeDeserializer, and 5 TypeIdResolver implementations. Top 20 recent-change hot-spots identified (PropertyValueBuffer @ 8 commits leads). Cross-referenced top 10 coarse hot-spots against 110 closed bugs, finding strong signal on BeanDeserializer (66 mentions, 14 bugs), BeanDeserializerBase (20 mentions, 9 bugs), and number/enum handling classes (case-insensitivity, +INF/-INF, EnumMap/Set issues). Semgrep + SpotBugs baselines unavailable — install them before Day 3 if you want the `dupe_of_baseline` validation gate working.
