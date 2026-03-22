"""Boundary detection and summary processing for conversation topic shifts.

This package provides:
- detect_boundary(): Detect whether a message continues the current topic
- BoundaryResult: Result of boundary detection with optional session resumption
- SessionCandidate: A candidate session for potential resumption
- SummaryProcessor: Per-message processor for rolling conversation summaries
"""

from tachikoma.boundary.detector import BoundaryResult, SessionCandidate, detect_boundary
from tachikoma.boundary.summary import SummaryProcessor

__all__ = ["detect_boundary", "BoundaryResult", "SessionCandidate", "SummaryProcessor"]
