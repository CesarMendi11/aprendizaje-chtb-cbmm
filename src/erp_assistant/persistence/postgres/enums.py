from enum import StrEnum


class ImportStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class KnowledgeVersionStatus(StrEnum):
    IMPORTED = "imported"
    ACTIVE = "active"
    ARCHIVED = "archived"
    FAILED = "failed"


class ReviewActionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CORRECT = "correct"
    RESET_TO_PENDING = "reset_to_pending"


class RemovalReconciliationDecisionType(StrEnum):
    RETAIN_FROM_ACTIVE = "retain_from_active"
    CONFIRMED_REMOVE = "confirmed_remove"
    UNRESOLVED = "unresolved"


class RemovalReviewActionType(StrEnum):
    CONFIRM_RETAIN = "confirm_retain"
    CONFIRM_REMOVE = "confirm_remove"
    RESET_TO_PENDING = "reset_to_pending"


class ReviewSource(StrEnum):
    CLI = "cli"
    API = "api"
    MIGRATION = "migration"
    CARRY_FORWARD = "carry_forward"


class SemanticType(StrEnum):
    SCREEN_PURPOSE = "screen_purpose"


class SemanticLifecycleOrigin(StrEnum):
    GENERATED = "generated"
    CARRIED_FORWARD = "carried_forward"
    REINFERRED = "reinferred"


class PipelineJobKind(StrEnum):
    CRAWL = "crawl"
    CANONICAL_BUILD = "canonical_build"
    CANONICAL_MERGE = "canonical_merge"
    CANONICAL_IMPORT = "canonical_import"
    CANONICAL_RECONCILIATION = "canonical_reconciliation"
    NEO4J_SYNC = "neo4j_sync"
    CHROMA_SYNC = "chroma_sync"
    SEMANTIC_INFERENCE = "semantic_inference"
    SEMANTIC_SYNC = "semantic_sync"


class PipelineJobScope(StrEnum):
    FULL = "full"
    MODULE = "module"
    SCREEN = "screen"
    VERSION = "version"
    SYSTEM = "system"


class PipelineJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SyncTarget(StrEnum):
    NEO4J = "neo4j"
    CHROMADB = "chromadb"


class SyncStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
