class DatasetInspectionError(RuntimeError):
    """Base exception for dataset inspection failures."""


class InspectionInputError(DatasetInspectionError):
    """Raised when a job does not reference a usable completed upload."""


class InspectionAuthorizationError(DatasetInspectionError):
    """Raised when a job creator cannot inspect the requested upload."""


class UploadMaterializationError(DatasetInspectionError):
    """Raised when an uploaded object cannot be materialized safely."""


class UnsupportedDatasetFormat(DatasetInspectionError):
    """Raised when the uploaded file is outside the supported inspection formats."""


class UnsafeArchiveError(DatasetInspectionError):
    """Raised when a compressed vector upload violates archive safety rules."""
