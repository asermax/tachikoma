"""Boundary detection and summary processing for conversation topic shifts.

This package provides:
- detect_boundary(): Detect whether a message continues the current topic
- SummaryProcessor: Per-message processor for rolling conversation summaries
"""

from tachikoma.boundary.detector import detect_boundary
from tachikoma.boundary.summary import SummaryProcessor

__all__ = ["detect_boundary", "SummaryProcessor"]
