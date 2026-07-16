from enum import StrEnum


class ReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class ControlType(StrEnum):
    BUTTON = "button"
    LINK = "link"
    TAB = "tab"
    DROPDOWN = "dropdown"
    DATE_PICKER = "date_picker"
    PAGINATION = "pagination"
    MENU = "menu"
    MODAL_TRIGGER = "modal_trigger"
    OTHER = "other"


class EvidenceType(StrEnum):
    STRUCTURAL_JSON = "structural_json"
    HTML = "html"
    SCREENSHOT = "screenshot"
    EVENT_AUDIT = "event_audit"
    TRANSITION_AUDIT = "transition_audit"
    OTHER = "other"


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
