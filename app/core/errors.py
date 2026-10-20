"""Domain exceptions mapped to HTTP errors by the API layer."""


class DomainError(Exception):
    status_code = 400
    code = "DOMAIN_ERROR"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"


class Conflict(DomainError):
    status_code = 409
    code = "CONFLICT"


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"


class Forbidden(DomainError):
    status_code = 403
    code = "FORBIDDEN"
