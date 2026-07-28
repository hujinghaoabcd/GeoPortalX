class DatasetRegistrationError(RuntimeError):
    """Raised when inspection output cannot become a persistent dataset."""


class VectorImportError(RuntimeError):
    """Raised when a vector source cannot be imported safely into PostGIS."""
