"""Tests for the pinned, offline CPU NLI adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

torch = pytest.importorskip("torch")

from aaos.inference.nli import (  # noqa: E402
    NLI_MODEL_REPOSITORY,
    NLI_MODEL_REVISION,
    NLI_SNAPSHOT_MANIFEST_SHA256,
    NliDecision,
    NliInputOverflowError,
    NliModelUnavailableError,
    PinnedCpuNliClassifier,
)


class _Config:
    id2label = {0: "neutral", 1: "ENTAILMENT", 2: "contradiction"}
    label2id = {"neutral": 0, "ENTAILMENT": 1, "contradiction": 2}
    max_position_embeddings = 32


class _Tokenizer:
    model_max_length = 32

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def __call__(self, premise: str, hypothesis: str, **kwargs: object) -> dict[str, object]:
        self.calls.append((premise, hypothesis, kwargs))
        return {"input_ids": torch.ones((1, 4), dtype=torch.long)}


class _Model:
    config = _Config()

    def __init__(self, logits: tuple[float, float, float] = (0.1, 3.0, 0.2)) -> None:
        self.logits = torch.tensor([logits], dtype=torch.float32)
        self.to_devices: list[str] = []
        self.eval_calls = 0
        self.grad_enabled: list[bool] = []

    def to(self, device: str) -> "_Model":
        self.to_devices.append(device)
        return self

    def eval(self) -> "_Model":
        self.eval_calls += 1
        return self

    def __call__(self, **_encoded: object) -> object:
        self.grad_enabled.append(torch.is_grad_enabled())
        return type("Output", (), {"logits": self.logits})()


def _classifier(
    tokenizer: _Tokenizer | None = None,
    model: _Model | None = None,
) -> tuple[PinnedCpuNliClassifier, _Tokenizer, _Model]:
    actual_tokenizer = tokenizer or _Tokenizer()
    actual_model = model or _Model()
    classifier = PinnedCpuNliClassifier(
        tokenizer_loader=lambda **_loader_kwargs: actual_tokenizer,
        model_loader=cast(Any, lambda **_loader_kwargs: actual_model),
    )
    return classifier, actual_tokenizer, actual_model


def test_pinned_identity_and_local_only_loaders_are_used() -> None:
    classifier, tokenizer, model = _classifier()

    result = classifier.classify("trusted evidence", "immutable requirement")

    assert result.decision is NliDecision.ENTAILED
    assert classifier.repository == NLI_MODEL_REPOSITORY
    assert classifier.revision == NLI_MODEL_REVISION
    assert classifier.snapshot_manifest_sha256 == NLI_SNAPSHOT_MANIFEST_SHA256
    assert tokenizer.calls[0][2]["truncation"] is False
    assert tokenizer.calls[0][2]["return_tensors"] == "pt"
    assert model.to_devices == ["cpu"]
    assert model.eval_calls == 1
    assert model.grad_enabled == [False]


def test_premise_precedes_hypothesis_and_no_extra_context_is_added() -> None:
    classifier, tokenizer, _ = _classifier()

    classifier.classify("s004 text\n\ns005 text", "r1 requirement")

    assert tokenizer.calls == [
        (
            "s004 text\n\ns005 text",
            "r1 requirement",
            {"return_tensors": "pt", "truncation": False, "padding": False},
        )
    ]


@pytest.mark.parametrize(
    ("labels", "label2id", "logits", "expected"),
    [
        (
            {0: "neutral", 1: "ENTAILMENT", 2: "contradiction"},
            {"neutral": 0, "ENTAILMENT": 1, "contradiction": 2},
            (0.1, 4.0, 0.2),
            NliDecision.ENTAILED,
        ),
        (
            {0: "contradiction", 1: "neutral", 2: "entailment"},
            {"contradiction": 0, "neutral": 1, "entailment": 2},
            (4.0, 1.0, 0.2),
            NliDecision.NOT_ENTAILED,
        ),
    ],
)
def test_native_argmax_is_mapped_by_dynamic_labels(
    labels: dict[int, str],
    label2id: dict[str, int],
    logits: tuple[float, float, float],
    expected: NliDecision,
) -> None:
    config = type("Config", (), {"id2label": labels, "label2id": label2id})()
    model = _Model(logits)
    model.config = config
    classifier, _, _ = _classifier(model=model)

    assert classifier.classify("evidence", "requirement").decision is expected


@pytest.mark.parametrize(
    "labels",
    [
        {0: "entailment", 1: "neutral"},
        {0: "entailment", 1: "neutral", 2: "neutral"},
        {0: "entailment", 1: "neutral", 2: "other"},
        {1: "entailment", 2: "neutral", 3: "contradiction"},
    ],
)
def test_ambiguous_or_incompatible_label_mapping_fails_closed(
    labels: dict[int, str],
) -> None:
    config = type("Config", (), {"id2label": labels, "label2id": {}})()
    model = _Model()
    model.config = config
    classifier, _, _ = _classifier(model=model)

    with pytest.raises(NliModelUnavailableError):
        classifier.classify("evidence", "requirement")


def test_probability_values_are_diagnostic_and_argmax_controls_decision() -> None:
    classifier, _, _ = _classifier(model=_Model((0.2, 0.3, 8.0)))

    result = classifier.classify("evidence", "requirement")

    assert result.decision is NliDecision.NOT_ENTAILED
    assert result.entailment_probability < result.contradiction_probability
    assert result.native_label == "contradiction"


def test_missing_snapshot_fails_closed_without_a_download_or_fallback(tmp_path: Path) -> None:
    calls: list[str] = []

    def tokenizer_loader(**_kwargs: object) -> _Tokenizer:
        calls.append("tokenizer")
        raise AssertionError("loader must not be called")

    def model_loader(**_kwargs: object) -> _Model:
        calls.append("model")
        raise AssertionError("loader must not be called")

    classifier = PinnedCpuNliClassifier(
        snapshot_path=tmp_path / "missing",
        tokenizer_loader=tokenizer_loader,
        model_loader=cast(Any, model_loader),
    )

    with pytest.raises(NliModelUnavailableError):
        classifier.classify("evidence", "requirement")

    assert calls == []


def test_snapshot_manifest_mismatch_fails_closed(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    classifier = PinnedCpuNliClassifier(
        snapshot_path=snapshot,
        tokenizer_loader=lambda **_kwargs: _Tokenizer(),
        model_loader=cast(Any, lambda **_kwargs: _Model()),
    )

    with pytest.raises(NliModelUnavailableError):
        classifier.classify("evidence", "requirement")


def test_loaders_receive_exact_revision_and_local_files_only() -> None:
    tokenizer = _Tokenizer()
    model = _Model()
    loader_calls: list[dict[str, object]] = []

    def tokenizer_loader(**kwargs: object) -> _Tokenizer:
        loader_calls.append(kwargs)
        return tokenizer

    def model_loader(**kwargs: object) -> _Model:
        loader_calls.append(kwargs)
        return model

    classifier = PinnedCpuNliClassifier(
        tokenizer_loader=tokenizer_loader,
        model_loader=cast(Any, model_loader),
    )
    classifier.classify("evidence", "requirement")

    assert loader_calls == [
        {
            "pretrained": NLI_MODEL_REPOSITORY,
            "revision": NLI_MODEL_REVISION,
            "local_files_only": True,
        },
        {
            "pretrained": NLI_MODEL_REPOSITORY,
            "revision": NLI_MODEL_REVISION,
            "local_files_only": True,
        },
    ]


def test_model_and_tokenizer_are_reused_and_release_is_idempotent() -> None:
    classifier, _, model = _classifier()

    classifier.classify("one", "r1")
    classifier.classify("two", "r2")
    classifier.close()
    classifier.close()

    assert model.to_devices == ["cpu"]
    with pytest.raises(NliModelUnavailableError):
        classifier.classify("three", "r3")


def test_overflow_is_detected_without_truncation() -> None:
    tokenizer = _Tokenizer()
    tokenizer.model_max_length = 3
    classifier, _, _ = _classifier(tokenizer=tokenizer)

    with pytest.raises(NliInputOverflowError):
        classifier.classify("evidence", "requirement")

    assert tokenizer.calls[0][2]["truncation"] is False


def test_warm_up_loads_the_model_once_without_classifying() -> None:
    classifier, _, model = _classifier()

    assert classifier.model_loaded is False
    classifier.warm_up()
    classifier.warm_up()

    assert classifier.model_loaded is True
    assert model.to_devices == ["cpu"]
