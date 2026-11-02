"""Domain-level exceptions, kept separate from raw database errors so
route handlers can raise something meaningful and one central handler
can translate it into a consistent JSON error response.

Why this exists: without it, a route would either return a fake "success"
response with an error message inside it (like the original code did -
e.g. {"message": "Investor Not Found"} with an HTTP 200 status, which
tricks clients into thinking the request succeeded), or it would let a
raw Oracle exception bubble up and leak internal details to the caller.
These classes let a route just say "raise NotFoundError(...)" and have
main.py turn that into a proper 404 with a clean message.
"""


class AppError(Exception):
    """Base class for all handled application errors.

    Subclasses only need to override status_code and message - the
    exception handler in main.py catches AppError (and therefore any
    subclass of it) in one place.
    """
    status_code = 500
    message = "An unexpected error occurred."

    def __init__(self, message: str | None = None):
        # Allows raising with a custom message, e.g. NotFoundError(f"Investor '{id}' not found."),
        # while still falling back to the class-level default if none is given.
        self.message = message or self.message
        super().__init__(self.message)


class NotFoundError(AppError):
    """Raised when a requested record (investor, transaction, etc.) doesn't exist.
    Maps to HTTP 404."""
    status_code = 404
    message = "Resource not found."


class DuplicateError(AppError):
    """Raised when trying to create something that already exists
    (e.g. an investor_id that's already in use). Maps to HTTP 409 Conflict."""
    status_code = 409
    message = "Resource already exists."


class DatabaseUnavailableError(AppError):
    """Raised (or mapped to, from main.py) when Oracle itself can't be
    reached - as opposed to a query failing for a data reason.
    Maps to HTTP 503 Service Unavailable, which correctly tells clients
    "try again later" rather than "your request was bad"."""
    status_code = 503
    message = "Database is currently unavailable. Please try again shortly."
