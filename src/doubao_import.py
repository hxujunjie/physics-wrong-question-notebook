"""Backward-compatible export of the recognition-result importer."""
from .recognition_import import (  # noqa: F401
    LOW_CONFIDENCE,
    SUPPORTED_SCHEMA_VERSIONS,
    ImportIssue,
    crop_imported_question,
    import_recognition_result,
    resolve_input,
    validate_recognition,
    _resolve_pdf_index,
)

__all__ = [
    "LOW_CONFIDENCE",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ImportIssue",
    "crop_imported_question",
    "import_recognition_result",
    "resolve_input",
    "validate_recognition",
    "_resolve_pdf_index",
]
