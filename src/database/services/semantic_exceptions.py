class SemanticDomainError(Exception):
    """Base para errores públicos del dominio semántico."""


class SemanticProposalNotFoundError(SemanticDomainError):
    pass


class SemanticScreenNotFoundError(SemanticDomainError):
    pass


class SemanticEntityTypeError(SemanticDomainError):
    pass


class SemanticVersionMismatchError(SemanticDomainError):
    pass


class SemanticScreenReviewError(SemanticDomainError):
    pass


class SemanticRevisionConflictError(SemanticDomainError):
    pass


class SemanticTransitionError(SemanticDomainError):
    pass


class SemanticPayloadError(SemanticDomainError):
    pass


class SemanticSensitiveContentError(SemanticPayloadError):
    pass


class SemanticIdentityCollisionError(SemanticDomainError):
    pass


class SemanticHistoryIntegrityError(SemanticDomainError):
    pass
