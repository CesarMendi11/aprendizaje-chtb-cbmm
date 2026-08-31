class ScreenPurposeGenerationError(Exception):
    def __init__(
        self,
        message,
        *,
        stage=None,
        location=(),
        category=None,
        value_length=None,
        value_type=None,
    ):
        super().__init__(message)
        self.stage = stage
        self.location = tuple(str(part) for part in location)
        self.category = category
        self.value_length = value_length
        self.value_type = value_type


class OllamaUnavailableError(ScreenPurposeGenerationError):
    pass


class OllamaTimeoutError(ScreenPurposeGenerationError):
    pass


class OllamaHTTPError(ScreenPurposeGenerationError):
    pass


class OllamaResponseTooLargeError(ScreenPurposeGenerationError):
    pass


class OllamaBodyError(ScreenPurposeGenerationError):
    pass


class EmptyStructuredOutputError(ScreenPurposeGenerationError):
    pass


class StructuredModeUnsupportedError(ScreenPurposeGenerationError):
    pass


class InferenceJSONError(ScreenPurposeGenerationError):
    pass


class InferenceSchemaError(ScreenPurposeGenerationError):
    pass


class InferenceScreenMismatchError(ScreenPurposeGenerationError):
    pass


class InferenceReferenceError(ScreenPurposeGenerationError):
    pass


class InferenceSensitiveContentError(ScreenPurposeGenerationError):
    pass


class InferenceNarrativeQualityError(ScreenPurposeGenerationError):
    pass


class InferenceGroundingError(ScreenPurposeGenerationError):
    pass


class InferenceUnsupportedActionError(InferenceGroundingError):
    pass


class InferencePurposeGroundingError(InferenceGroundingError):
    pass
