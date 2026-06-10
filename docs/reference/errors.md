# Errors reference catalog

Every exception the framework raises has a dedicated reference
page. The error message in the runtime ends with a `More:` link to
its page; you should rarely need to visit this catalog directly.

If you arrived here from an error message, follow the link the
message printed. If you're browsing — start with
[ReplayDivergenceError](errors/replay-divergence-error.md) (the
voice reference for the catalog) or
[UnsupportedPatternError](errors/unsupported-pattern-error.md) (the
authoring guide for pattern subscriptions).

The hierarchy itself is documented at
[Concepts: Failure model](../concepts/failure-model.md).
The event payload reason-code vocabulary is documented at
[Reference: Reason codes](reason-codes.md).

## By category

The seven category bases match the
[`ActiveGraphError` hierarchy](api/errors.md):

### ReplayError

- [ReplayDivergenceError](errors/replay-divergence-error.md)

### PatternError

- [UnsupportedPatternError](errors/unsupported-pattern-error.md)
- [InvalidActivateAfter](errors/invalid-activate-after.md)

### StorageError

- [CorruptedEventPayloadError](errors/corrupted-event-payload-error.md)
- [DuplicateEventError](errors/duplicate-event-error.md)
- [EventNotFoundError](errors/event-not-found-error.md)
- [InvalidStoreURL](errors/invalid-store-url-error.md)
- [NonSerializableEventError](errors/non-serializable-event-error.md)
- [SchemaVersionMismatch](errors/schema-version-mismatch.md)

### ExecutionError

- [LLMBehaviorError](errors/llm-behavior-error.md)
- [ToolError](errors/tool-error.md)
- [UnknownToolError](errors/unknown-tool-error.md)
- [ApprovalNotFoundError](errors/approval-not-found-error.md)

### ConfigurationError

- [MissingProviderError](errors/missing-provider-error.md)
- [MissingToolError](errors/missing-tool-error.md)
- [MissingOptionalDependency](errors/missing-optional-dependency.md)
- [InvalidToolRegistration](errors/invalid-tool-registration.md)
- [InvalidRuntimeConfiguration](errors/invalid-runtime-configuration.md)
- [InvalidArgumentType](errors/invalid-argument-type.md)
- [RuntimeContextRequiredError](errors/runtime-context-required-error.md)
- [InvalidPatchLifecycleState](errors/invalid-patch-lifecycle-state.md)

### RegistrationError

- [BehaviorNotFoundError](errors/behavior-not-found-error.md)
- [AmbiguousBehaviorError](errors/ambiguous-behavior-error.md)
- [ToolNotFoundError](errors/tool-not-found-error.md)
- [AmbiguousToolError](errors/ambiguous-tool-error.md)
- [IncompatibleRuntimeState](errors/incompatible-runtime-state.md)

### PackError

- [PackNotFoundError](errors/pack-not-found-error.md)
- [PackConflictError](errors/pack-conflict-error.md)
- [PackVersionConflictError](errors/pack-version-conflict-error.md)
- [PackSchemaViolation](errors/pack-schema-violation.md)

### Internal (framework-bug voice)

- [InternalEvaluatorError](errors/internal-evaluator-error.md)

## What's related

- [Concepts: Failure model](../concepts/failure-model.md) — the
  hierarchy and the events-not-exceptions principle.
- [Reference: Reason codes](reason-codes.md) — the stable
  `behavior.failed` / `tool.responded` / failed LLM attempt reason
  vocabulary.
- [API reference: Errors](api/errors.md) — the class hierarchy
  rendered from docstrings.
- [Cookbook: Debugging](../cookbook/debugging.md) — diagnostic
  workflows that build on the per-error catalog.
