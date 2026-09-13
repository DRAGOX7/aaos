"""Versioned deterministic calibration and heldout manifests."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Mapping, Sequence, cast

CALIBRATION_CASE_COUNT = 48
HELDOUT_CASE_COUNT = 24
MANIFEST_VERSION = "phase6a-trusted-core-v1"
Owner = Literal["approval", "capability", "claim", "dispatch", "outcome", "security", "import"]
LanguageFixture = Literal["en", "ar"]


class EvaluationSetup(StrEnum):
    PREPARED = "PREPARED"
    APPROVAL_PENDING = "APPROVAL_PENDING"
    APPROVED = "APPROVED"
    CLAIMED = "CLAIMED"
    EXECUTING = "EXECUTING"
    RECONCILIATION = "RECONCILIATION"
    ADVERSARIAL_TEXT = "ADVERSARIAL_TEXT"
    IMPORT_SMOKE = "IMPORT_SMOKE"


class EvaluationInvariant(StrEnum):
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CAPABILITY_REVALIDATED = "CAPABILITY_REVALIDATED"
    SINGLE_CLAIM = "SINGLE_CLAIM"
    DURABLE_DISPATCH = "DURABLE_DISPATCH"
    TRUSTFUL_OUTCOME = "TRUSTFUL_OUTCOME"
    SECURITY_ISOLATION = "SECURITY_ISOLATION"
    IMPORT_SAFE = "IMPORT_SAFE"


class EvaluationCategory(StrEnum):
    APPROVAL_EXPIRY = "approval_expiry"
    CAPABILITY_POLICY = "capability_policy"
    CLAIM_CONCURRENCY = "claim_concurrency"
    DISPATCH_PRECONDITION = "dispatch_precondition"
    OUTCOME_RECONCILIATION = "outcome_reconciliation"
    SECURITY_IMPORT = "security_import"


@dataclass(frozen=True, slots=True)
class Phase6AEvaluationCase:
    case_id: str
    owner: Owner
    category: EvaluationCategory
    language_fixture: LanguageFixture | None
    setup: EvaluationSetup
    expected_invariant: EvaluationInvariant

    def to_dict(self) -> dict[str, str | None]:
        return {"case_id": self.case_id, "owner": self.owner, "category": self.category.value,
                "language_fixture": self.language_fixture, "setup": self.setup.value,
                "expected_invariant": self.expected_invariant.value}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Phase6AEvaluationCase:
        expected = {"case_id", "owner", "category", "language_fixture", "setup", "expected_invariant"}
        if set(raw) != expected:
            raise ValueError("evaluation case has unknown or missing fields")
        language = raw["language_fixture"]
        if language is not None and language not in ("en", "ar"):
            raise ValueError("invalid language fixture")
        owner = raw["owner"]
        owners = ("approval", "capability", "claim", "dispatch", "outcome", "security", "import")
        if owner not in owners:
            raise ValueError("invalid case owner")
        return cls(_require_text(raw["case_id"], "case_id", 64), cast(Owner, owner),
                   EvaluationCategory(raw["category"]), cast(LanguageFixture | None, language),
                   EvaluationSetup(raw["setup"]), EvaluationInvariant(raw["expected_invariant"]))


@dataclass(frozen=True, slots=True)
class FrozenEvaluationManifest:
    version: str
    cases: Sequence[Phase6AEvaluationCase]
    manifest_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "cases", tuple(self.cases))

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "cases": [case.to_dict() for case in self.cases],
                "manifest_sha256": self.manifest_sha256}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> FrozenEvaluationManifest:
        if set(raw) != {"version", "cases", "manifest_sha256"}:
            raise ValueError("manifest has unknown or missing fields")
        cases_raw = raw["cases"]
        if not isinstance(cases_raw, list):
            raise TypeError("manifest cases must be a list")
        manifest = freeze_manifest(tuple(Phase6AEvaluationCase.from_dict(case) for case in cases_raw),
                                   version=_require_text(raw["version"], "version", 64))
        if manifest.manifest_sha256 != raw["manifest_sha256"]:
            raise ValueError("manifest fingerprint mismatch")
        return manifest


def _require_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be a bounded non-empty string")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def freeze_manifest(cases: Sequence[Phase6AEvaluationCase], *, version: str) -> FrozenEvaluationManifest:
    version = _require_text(version, "version", 64)
    if version != MANIFEST_VERSION:
        raise ValueError("manifest version is not the frozen Phase 6A version")
    ordered = tuple(sorted(cases, key=lambda case: case.case_id))
    if len({case.case_id for case in ordered}) != len(ordered):
        raise ValueError("manifest case IDs must be unique")
    for case in ordered:
        if not isinstance(case, Phase6AEvaluationCase):
            raise TypeError("manifest cases must be Phase6AEvaluationCase values")
        _require_text(case.case_id, "case_id", 64)
        if not re.fullmatch(r"(?:cal|hold)-\d{3}", case.case_id):
            raise ValueError("case_id must use the cal-NNN or hold-NNN form")
        if case.owner not in ("approval", "capability", "claim", "dispatch", "outcome", "security", "import"):
            raise ValueError("invalid case owner")
        if not isinstance(case.category, EvaluationCategory):
            raise TypeError("case category must be an EvaluationCategory")
        if case.language_fixture is not None and case.language_fixture not in ("en", "ar"):
            raise ValueError("invalid language fixture")
        if not isinstance(case.setup, EvaluationSetup):
            raise TypeError("case setup must be an EvaluationSetup")
        if not isinstance(case.expected_invariant, EvaluationInvariant):
            raise TypeError("case invariant must be an EvaluationInvariant")
    prefixes = {case.case_id.split("-", 1)[0] for case in ordered}
    if prefixes not in ({"cal"}, {"hold"}):
        raise ValueError("manifest must contain exactly one canonical split")
    _validate_canonical_matrix(ordered, next(iter(prefixes)))
    payload = _canonical_json({"version": version, "cases": [case.to_dict() for case in ordered]}).encode()
    return FrozenEvaluationManifest(version, ordered, hashlib.sha256(payload).hexdigest())


_CATEGORY_SPECS: tuple[tuple[EvaluationCategory, Owner, EvaluationInvariant, tuple[EvaluationSetup, ...]], ...] = (
    (EvaluationCategory.APPROVAL_EXPIRY, "approval", EvaluationInvariant.APPROVAL_REQUIRED,
     (EvaluationSetup.PREPARED, EvaluationSetup.APPROVAL_PENDING, EvaluationSetup.APPROVED,
      EvaluationSetup.APPROVAL_PENDING, EvaluationSetup.APPROVED, EvaluationSetup.APPROVAL_PENDING,
      EvaluationSetup.APPROVED, EvaluationSetup.APPROVAL_PENDING)),
    (EvaluationCategory.CAPABILITY_POLICY, "capability", EvaluationInvariant.CAPABILITY_REVALIDATED,
     (EvaluationSetup.PREPARED, EvaluationSetup.APPROVAL_PENDING, EvaluationSetup.APPROVED,
      EvaluationSetup.PREPARED, EvaluationSetup.APPROVAL_PENDING, EvaluationSetup.APPROVED,
      EvaluationSetup.PREPARED, EvaluationSetup.APPROVED)),
    (EvaluationCategory.CLAIM_CONCURRENCY, "claim", EvaluationInvariant.SINGLE_CLAIM,
     (EvaluationSetup.APPROVED, EvaluationSetup.CLAIMED) * 4),
    (EvaluationCategory.DISPATCH_PRECONDITION, "dispatch", EvaluationInvariant.DURABLE_DISPATCH,
     (EvaluationSetup.CLAIMED, EvaluationSetup.EXECUTING) * 4),
    (EvaluationCategory.OUTCOME_RECONCILIATION, "outcome", EvaluationInvariant.TRUSTFUL_OUTCOME,
     (EvaluationSetup.EXECUTING, EvaluationSetup.RECONCILIATION) * 4),
)


def _build_cases(prefix: Literal["cal", "hold"], group_size: int) -> tuple[Phase6AEvaluationCase, ...]:
    cases: list[Phase6AEvaluationCase] = []
    number = 1
    for category, owner, invariant, setups in _CATEGORY_SPECS:
        for offset in range(group_size):
            cases.append(Phase6AEvaluationCase(f"{prefix}-{number:03d}", owner, category, None,
                                               setups[offset % len(setups)], invariant))
            number += 1
    security_count = 4 if prefix == "cal" else 2
    for offset in range(group_size):
        security = offset < security_count
        cases.append(Phase6AEvaluationCase(
            f"{prefix}-{number:03d}", "security" if security else "import",
            EvaluationCategory.SECURITY_IMPORT,
            "en" if offset < group_size // 2 else "ar",
            EvaluationSetup.ADVERSARIAL_TEXT if security else EvaluationSetup.IMPORT_SMOKE,
            EvaluationInvariant.SECURITY_ISOLATION if security else EvaluationInvariant.IMPORT_SAFE))
        number += 1
    return tuple(cases)


def _validate_canonical_matrix(
    cases: Sequence[Phase6AEvaluationCase], prefix: Literal["cal", "hold"] | str
) -> None:
    """Validate labels against the independently declared frozen ID ranges."""

    group_size = 8 if prefix == "cal" else 4
    expected_count = CALIBRATION_CASE_COUNT if prefix == "cal" else HELDOUT_CASE_COUNT
    if len(cases) != expected_count:
        raise ValueError("canonical manifest case count changed")
    if tuple(case.case_id for case in cases) != tuple(
        f"{prefix}-{index:03d}" for index in range(1, expected_count + 1)
    ):
        raise ValueError("canonical manifest IDs changed")
    for case in cases:
        index = int(case.case_id.rsplit("-", 1)[1])
        if index <= group_size:
            spec_index = 0
        elif index <= group_size * 2:
            spec_index = 1
        elif index <= group_size * 3:
            spec_index = 2
        elif index <= group_size * 4:
            spec_index = 3
        elif index <= group_size * 5:
            spec_index = 4
        else:
            offset = index - (group_size * 5) - 1
            security_count = 4 if prefix == "cal" else 2
            expected_owner = "security" if offset < security_count else "import"
            expected_setup = (
                EvaluationSetup.ADVERSARIAL_TEXT
                if offset < security_count
                else EvaluationSetup.IMPORT_SMOKE
            )
            expected_invariant = (
                EvaluationInvariant.SECURITY_ISOLATION
                if offset < security_count
                else EvaluationInvariant.IMPORT_SAFE
            )
            expected_language = "en" if offset < group_size // 2 else "ar"
            if (case.owner, case.setup, case.expected_invariant, case.language_fixture) != (
                expected_owner,
                expected_setup,
                expected_invariant,
                expected_language,
            ):
                raise ValueError("case labels do not match the canonical manifest")
            continue
        category, owner, invariant, setups = _CATEGORY_SPECS[spec_index]
        offset = (index - 1) % group_size
        if (case.category, case.owner, case.expected_invariant, case.setup, case.language_fixture) != (
            category,
            owner,
            invariant,
            setups[offset % len(setups)],
            None,
        ):
            raise ValueError("case labels do not match the canonical manifest")


def _validate_built_matrix(cases: Sequence[Phase6AEvaluationCase], *, prefix: Literal["cal", "hold"], expected: int) -> tuple[Phase6AEvaluationCase, ...]:
    if len(cases) != expected:
        raise AssertionError(f"{prefix} manifest has the wrong case count")
    if tuple(case.case_id for case in cases) != tuple(f"{prefix}-{index:03d}" for index in range(1, expected + 1)):
        raise AssertionError("manifest IDs are not deterministic")
    per_category = 8 if prefix == "cal" else 4
    expected_counts = {category.value: per_category for category, *_ in _CATEGORY_SPECS}
    expected_counts[EvaluationCategory.SECURITY_IMPORT.value] = per_category
    if category_counts(cases) != expected_counts:
        raise AssertionError("manifest category matrix changed")
    if prefix == "cal":
        expected_languages: Mapping[str, Sequence[str]] = {
            "en": ("cal-041", "cal-042", "cal-043", "cal-044"),
            "ar": ("cal-045", "cal-046", "cal-047", "cal-048"),
        }
    else:
        expected_languages = {"en": ("hold-021", "hold-022"), "ar": ("hold-023", "hold-024")}
    if language_fixture_ids(cases) != expected_languages:
        raise AssertionError("manifest language-fixture matrix changed")
    return tuple(cases)


def build_calibration_cases() -> Sequence[Phase6AEvaluationCase]:
    return _validate_built_matrix(_build_cases("cal", 8), prefix="cal", expected=CALIBRATION_CASE_COUNT)


def build_heldout_cases() -> Sequence[Phase6AEvaluationCase]:
    return _validate_built_matrix(_build_cases("hold", 4), prefix="hold", expected=HELDOUT_CASE_COUNT)


def category_counts(cases: Sequence[Phase6AEvaluationCase]) -> Mapping[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category.value] = counts.get(case.category.value, 0) + 1
    return counts


def language_fixture_ids(cases: Sequence[Phase6AEvaluationCase]) -> Mapping[str, Sequence[str]]:
    result: dict[str, list[str]] = {}
    for case in cases:
        if case.language_fixture is not None:
            result.setdefault(case.language_fixture, []).append(case.case_id)
    return {language: tuple(ids) for language, ids in result.items()}
