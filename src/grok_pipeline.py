"""Backward-compatible export of the multi-vendor recognition pipeline."""
from .recognition_pipeline import *  # noqa: F403
from .recognition_pipeline import (
    PAGE_BATCH_SIZE,
    PHOTO_EXTENSIONS,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    LOW_CONFIDENCE,
    BudgetExceeded,
    GrokRecognitionJob,
    RecognitionJob,
    cache_root,
    discover_students,
    make_output_dir,
    preflight,
)

__all__ = [
    "PAGE_BATCH_SIZE",
    "PHOTO_EXTENSIONS",
    "PROMPT_VERSION",
    "SCHEMA_VERSION",
    "LOW_CONFIDENCE",
    "BudgetExceeded",
    "GrokRecognitionJob",
    "RecognitionJob",
    "cache_root",
    "discover_students",
    "make_output_dir",
    "preflight",
]
