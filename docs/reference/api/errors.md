# Errors

The `ActiveGraphError` hierarchy plus the cross-cutting helpers.
For the format spec and the events-not-exceptions principle see
[`concepts/failure-model`](../../concepts/failure-model.md). For
per-error recovery prose see the
[error reference](../errors/replay-divergence-error.md) catalog
(one page per leaf).

## Hierarchy root

::: activegraph.ActiveGraphError

## Category bases

::: activegraph.ConfigurationError

::: activegraph.RegistrationError

::: activegraph.ExecutionError

::: activegraph.ReplayError

::: activegraph.StorageError

::: activegraph.PatternError

::: activegraph.PackError

## Cross-cutting

::: activegraph.MissingOptionalDependency

## Replay

::: activegraph.ReplayDivergenceError

## Pattern

::: activegraph.UnsupportedPatternError

## Storage

::: activegraph.NonSerializableEventError

::: activegraph.InvalidStoreURL

::: activegraph.SchemaVersionMismatch

::: activegraph.EventNotFoundError

::: activegraph.DuplicateEventError

::: activegraph.CorruptedEventPayloadError

::: activegraph.ConcurrentWriterError

## Execution

::: activegraph.LLMBehaviorError

::: activegraph.ToolError

::: activegraph.UnknownToolError

::: activegraph.ApprovalNotFoundError

::: activegraph.ReservedFieldError

::: activegraph.InvalidPatchOperation

::: activegraph.RuntimeContextRequiredError

::: activegraph.InvalidPatchLifecycleState

::: activegraph.InternalEvaluatorError

::: activegraph.PromoteLineageError

::: activegraph.PromoteConflictError

## Registration

::: activegraph.MissingProviderError

::: activegraph.MissingToolError

::: activegraph.BehaviorNotFoundError

::: activegraph.AmbiguousBehaviorError

::: activegraph.ToolNotFoundError

::: activegraph.AmbiguousToolError

::: activegraph.InvalidActivateAfter

::: activegraph.InvalidToolRegistration

::: activegraph.PackNotFoundError

::: activegraph.PackConflictError

::: activegraph.PackVersionConflictError

## Pack

::: activegraph.PackSchemaViolation

::: activegraph.PackValidationError

::: activegraph.PackSettingsMissingError

::: activegraph.PackPromptLoadError

## Configuration

::: activegraph.InvalidRuntimeConfiguration

::: activegraph.InvalidArgumentType

::: activegraph.IncompatibleRuntimeState
