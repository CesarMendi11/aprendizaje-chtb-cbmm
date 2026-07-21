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


class ReviewSource(StrEnum):
    CLI = "cli"
    API = "api"
    MIGRATION = "migration"
    CARRY_FORWARD = "carry_forward"


class SemanticType(StrEnum):
    SCREEN_PURPOSE = "screen_purpose"


class SyncTarget(StrEnum):
    NEO4J = "neo4j"
    CHROMADB = "chromadb"


class SyncStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
