"""Active Graph Runtime. Public API surface.

The graph is the world. Behaviors are physics. The trace is the proof.
"""

from activegraph.behaviors.base import Behavior, LLMBehavior, RelationBehavior
from activegraph.behaviors.decorators import (
    behavior,
    clear_registry,
    get_registry,
    llm_behavior,
    register,
    relation_behavior,
)
from activegraph.core.clock import Clock, FrozenClock, TickingClock
from activegraph.core.event import Event
from activegraph.core.graph import Graph, Object, Relation
from activegraph.core.ids import IDGen
from activegraph.core.patch import Patch
from activegraph.core.view import View
from activegraph.errors import (
    ActiveGraphError,
    ConfigurationError,
    ExecutionError,
    MissingOptionalDependency,
    PackError,
    PatternError,
    RegistrationError,
    ReplayError,
    StorageError,
)
from activegraph.runtime.registration_errors import (
    AmbiguousBehaviorError,
    AmbiguousToolError,
    BehaviorNotFoundError,
    InvalidToolRegistration,
    ToolNotFoundError,
)
from activegraph.runtime.scheduler import InvalidActivateAfter
from activegraph.frame import Frame
from activegraph.llm.errors import LLMBehaviorError, MissingProviderError
from activegraph.policy import Policy
from activegraph.runtime.authority import AuthorityDecision
from activegraph.runtime.budget import Budget
from activegraph.runtime.diff import Diff, DivergentObject, DivergentRelation
from activegraph.runtime.dev_override import DevOverride
from activegraph.runtime.promote import (
    PromoteConflict,
    PromotePlan,
    PromoteResult,
)
from activegraph.runtime.config_errors import (
    IncompatibleRuntimeState,
    InvalidArgumentType,
    InvalidRuntimeConfiguration,
)
from activegraph.runtime.errors import ReplayDivergenceError
from activegraph.runtime.exec_errors import (
    ApprovalNotFoundError,
    InternalEvaluatorError,
    InvalidPatchLifecycleState,
    PromoteConflictError,
    PromoteLineageError,
    RuntimeContextRequiredError,
)
from activegraph.runtime.patterns import UnsupportedPatternError
from activegraph.runtime.runtime import BehaviorFailure, Runtime
from activegraph.sinks import (
    DeliveryContext,
    EventSink,
    JSONLEventSink,
    OverflowPolicy,
    RecordedDelivery,
    RecordingSink,
    SinkConfig,
    SinkHandle,
    SinkState,
    SinkStatus,
)
from activegraph.store import (
    CorruptedEventPayloadError,
    DuplicateEventError,
    EventNotFoundError,
    EventStore,
    FalkorDBGraphStore,
    GraphStore,
    InMemoryEventStore,
    InMemoryGraphStore,
    InvalidStoreURL,
    NonSerializableEventError,
    RunRecord,
    SQLiteEventStore,
    SchemaVersionMismatch,
    open_store,
    parse_store_url,
)
# v0.7 public surface for tools
from activegraph.tools import (
    MissingToolError,
    Tool,
    ToolContext,
    ToolError,
    UnknownToolError,
    clear_tool_registry,
    get_tool_registry,
    tool,
)
# v0.8 observability surface
from activegraph.observability import (
    Metrics,
    MigrationReport,
    MigrationRunReport,
    NoOpMetrics,
    OpenTelemetryMetrics,
    PrometheusMetrics,
    RuntimeStatus,
    configure_logging,
    migrate,
)
# v0.9 packs surface (top-level: the API for *using* packs from user code).
# Pack-aware decorators live under `activegraph.packs` and are intentionally
# NOT re-exported here — pack authors must import them from `activegraph.packs`
# so the import path makes the boundary explicit. CONTRACT v0.9 #3.
from activegraph.packs import (
    DiscoveredPack,
    EmptySettings,
    ObjectType,
    Pack,
    PackConflictError,
    PackError,
    PackNotFoundError,
    PackPolicy,
    PackPrompt,
    PackPromptLoadError,
    PackSchemaViolation,
    PackSettingsMissingError,
    PackValidationError,
    PackVersionConflictError,
    PendingApproval,
    RelationType,
    clear_discovery_cache,
    discover,
    load_by_name,
    load_prompts_from_dir,
)

__all__ = [
    "ActiveGraphError",
    "AmbiguousBehaviorError",
    "AmbiguousToolError",
    "ApprovalNotFoundError",
    "AuthorityDecision",
    "Behavior",
    "BehaviorFailure",
    "BehaviorNotFoundError",
    "Budget",
    "Clock",
    "ConfigurationError",
    "CorruptedEventPayloadError",
    "Diff",
    "DeliveryContext",
    "DevOverride",
    "DiscoveredPack",
    "DivergentObject",
    "DivergentRelation",
    "DuplicateEventError",
    "EmptySettings",
    "Event",
    "EventSink",
    "EventNotFoundError",
    "EventStore",
    "ExecutionError",
    "FalkorDBGraphStore",
    "Frame",
    "FrozenClock",
    "Graph",
    "GraphStore",
    "IDGen",
    "InMemoryEventStore",
    "InMemoryGraphStore",
    "IncompatibleRuntimeState",
    "InternalEvaluatorError",
    "InvalidActivateAfter",
    "InvalidArgumentType",
    "InvalidPatchLifecycleState",
    "InvalidRuntimeConfiguration",
    "InvalidStoreURL",
    "InvalidToolRegistration",
    "JSONLEventSink",
    "LLMBehavior",
    "LLMBehaviorError",
    "Metrics",
    "MigrationReport",
    "MigrationRunReport",
    "MissingOptionalDependency",
    "MissingProviderError",
    "MissingToolError",
    "NoOpMetrics",
    "NonSerializableEventError",
    "Object",
    "OpenTelemetryMetrics",
    "ObjectType",
    "OverflowPolicy",
    "Pack",
    "PackConflictError",
    "PackError",
    "PackNotFoundError",
    "PackPolicy",
    "PackPromptLoadError",
    "PackPrompt",
    "PackSchemaViolation",
    "PackSettingsMissingError",
    "PackValidationError",
    "PackVersionConflictError",
    "PatternError",
    "Patch",
    "PendingApproval",
    "Policy",
    "PrometheusMetrics",
    "PromoteConflict",
    "PromoteConflictError",
    "PromoteLineageError",
    "PromotePlan",
    "PromoteResult",
    "RecordedDelivery",
    "RecordingSink",
    "Relation",
    "RelationBehavior",
    "RegistrationError",
    "RelationType",
    "ReplayDivergenceError",
    "ReplayError",
    "RunRecord",
    "Runtime",
    "RuntimeContextRequiredError",
    "RuntimeStatus",
    "SQLiteEventStore",
    "SchemaVersionMismatch",
    "SinkConfig",
    "SinkHandle",
    "SinkState",
    "SinkStatus",
    "StorageError",
    "TickingClock",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolNotFoundError",
    "UnknownToolError",
    "UnsupportedPatternError",
    "View",
    "behavior",
    "clear_discovery_cache",
    "clear_registry",
    "clear_tool_registry",
    "configure_logging",
    "discover",
    "get_registry",
    "get_tool_registry",
    "llm_behavior",
    "load_by_name",
    "load_prompts_from_dir",
    "migrate",
    "open_store",
    "parse_store_url",
    "register",
    "relation_behavior",
    "tool",
]

__version__ = "1.9.0"
