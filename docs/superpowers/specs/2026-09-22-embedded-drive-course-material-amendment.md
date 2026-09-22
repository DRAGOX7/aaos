# Post-Phase-9 Embedded Google Drive Course Material Amendment

## Problem

Live AAOS acceptance on AI495C proved that Moodle course material may be
published through Google Drive embedded-folder iframes rather than Moodle
`mod/resource` activities.

The frozen Phase 2 contract intentionally recognizes only Moodle
`/mod/resource/view.php?id=...` activities as remote files and blocks
cross-origin browser collection. That behavior remains correct and MUST NOT
be weakened.

## Scope

Add a separate read-only provider-scoped discovery capability for Google
Drive folders explicitly embedded by an authenticated Moodle course page.

This amendment does not modify the existing Moodle resource parser,
Moodle browser request policy, or Phase 3 Moodle downloader.

## Trust boundary

An embedded Drive folder is eligible only when all of the following hold:

- it is referenced by the authenticated Moodle course HTML;
- scheme is exactly `https`;
- host is exactly `drive.google.com`;
- path is exactly `/embeddedfolderview`;
- it carries exactly one bounded Drive folder identity;
- arbitrary external URLs are never followed.

A discovered Drive file is eligible only when:

- it is observed inside an approved embedded Drive folder;
- scheme is exactly `https`;
- host is exactly `drive.google.com`;
- path has exact form `/file/d/<bounded-file-id>/view`;
- the title is nonblank, bounded, and safe;
- duplicate file identities in one course are deterministic.

## Identity

Drive files use a provider-distinct source identity:

`drive-file:<course-source-id>:<drive-file-id>`

They MUST NOT use `resource-cm:*`.

The first discovery slice stores metadata only. It performs no file download.

## Slice 1 acceptance

Given one Moodle course containing approved embedded Drive folders:

- native Moodle resources remain unchanged;
- top-level Drive files are converted to `RawRemoteFile`;
- folders themselves are not treated as files;
- nested-folder recursion is not implemented;
- no Drive download occurs;
- no RAG/index mutation occurs;
- existing Phase 2 and Phase 3 security tests remain green.

## Deferred

Separate later slices will cover:

- authorized Drive PDF download;
- recursive folder discovery;
- DOCX/PPTX/IPYNB ingestion;
- automatic incremental RAG indexing.
