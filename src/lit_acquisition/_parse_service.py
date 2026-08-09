"""Optional MinerU parse service integration.

The multilingual workflow uses MinerU for early PDF parsing before the
relevance gate.  When the ``parse_document`` module is not available
(e.g. when lit-acquisition is used standalone), batch parsing is skipped
and the relevance gate falls back to PyMuPDF text extraction.
"""

from __future__ import annotations


def create_parse_service():
    """Create a parse service instance.

    Returns:
        A parse service object with ``parse_local_files`` method.

    Raises:
        ImportError: When the parse_document module is not available.
    """
    try:
        from lit_acquisition_parse_document import create_parse_service as _create
    except ImportError as exc:
        raise ImportError(
            "MinerU parse service is not available. "
            "Install the 'lit-acquisition-parse' extra or use the workflow "
            "without early batch parsing."
        ) from exc
    return _create()
