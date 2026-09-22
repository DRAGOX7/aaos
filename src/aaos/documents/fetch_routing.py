"""Provider-confined routing for Phase 3 PDF fetches."""

from __future__ import annotations

import re
from typing import Protocol

from aaos.documents.contracts import (
    DocumentIngestionFailure,
    FetchedPdf,
    IngestionErrorCode,
    RemoteFileSelection,
)

_MOODLE_RESOURCE_ID = re.compile(
    r"resource-cm:[1-9][0-9]*\Z",
    re.ASCII,
)
_DRIVE_FILE_ID = re.compile(
    r"drive-file:[1-9][0-9]{0,19}:[A-Za-z0-9_-]{1,128}\Z",
    re.ASCII,
)


class _ProviderPdfFetcher(Protocol):
    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf: ...


class ProviderRoutingPdfFetcher:
    """Route only explicitly recognized Phase-3 PDF identities."""

    def __init__(
        self,
        *,
        moodle_fetcher: _ProviderPdfFetcher,
        drive_fetcher: _ProviderPdfFetcher,
    ) -> None:
        self._moodle_fetcher = moodle_fetcher
        self._drive_fetcher = drive_fetcher

    def supports(self, selection: RemoteFileSelection) -> bool:
        provider = _provider_for(selection)

        if provider == "moodle":
            return _provider_supports(
                self._moodle_fetcher,
                selection,
            )

        if provider == "drive":
            return _provider_supports(
                self._drive_fetcher,
                selection,
            )

        if _DRIVE_FILE_ID.fullmatch(selection.source_id) and selection.title.casefold().endswith(
            (".docx", ".pptx", ".ipynb")
        ):
            return False

        raise DocumentIngestionFailure(IngestionErrorCode.UNAUTHORIZED_ROUTE)

    def fetch(self, selection: RemoteFileSelection) -> FetchedPdf:
        provider = _provider_for(selection)

        if provider == "moodle":
            fetcher = self._moodle_fetcher
        elif provider == "drive":
            fetcher = self._drive_fetcher
        else:
            raise DocumentIngestionFailure(IngestionErrorCode.UNAUTHORIZED_ROUTE)

        if not _provider_supports(fetcher, selection):
            raise DocumentIngestionFailure(IngestionErrorCode.UNAUTHORIZED_ROUTE)

        return fetcher.fetch(selection)


def _provider_for(
    selection: RemoteFileSelection,
) -> str | None:
    source_id = selection.source_id

    if _MOODLE_RESOURCE_ID.fullmatch(source_id):
        return "moodle"

    if _DRIVE_FILE_ID.fullmatch(source_id) and selection.title.casefold().endswith(".pdf"):
        return "drive"

    return None


def _provider_supports(
    fetcher: _ProviderPdfFetcher,
    selection: RemoteFileSelection,
) -> bool:
    supports = getattr(fetcher, "supports", None)

    if supports is None:
        return True

    if not callable(supports):
        return False

    return supports(selection) is True


__all__ = ["ProviderRoutingPdfFetcher"]
