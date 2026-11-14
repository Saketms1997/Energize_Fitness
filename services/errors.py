class DomainError(Exception):
    """Base class for errors the UI can show to the owner as-is."""


class ValidationError(DomainError):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class DuplicateError(DomainError):
    pass


class NotFoundError(DomainError):
    pass


class BusinessRuleError(DomainError):
    pass
