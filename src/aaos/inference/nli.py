"""Pinned, offline CPU-only natural-language-inference adapter.

The adapter deliberately has no chat or network surface.  It loads the approved
sequence-classification snapshot lazily and exposes only a typed binary
entailment decision to the semantic-coverage layer.
"""

from __future__ import annotations

import gc
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast

NLI_MODEL_REPOSITORY = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
NLI_MODEL_REVISION = "8adb042d524ecd5c26d3e3ba0e3fbcf7e2d0864c"
NLI_SNAPSHOT_MANIFEST_SHA256 = "04e65c8a1034152d8cc44cfde15631e69fce9c9ac10b6aa8664fb264f8b7ed4d"
_SNAPSHOT_FILES: tuple[tuple[str, str, int], ...] = (
    (
        "added_tokens.json",
        "fb697283833d25e2c711f1bc37730ecd8b20f4bd5f015db1d84aefe0adc9155a",
        23,
    ),
    (
        "config.json",
        "4e4430c95100d613df80fa01276f231931e1b535557cf62fbb3cc50323e50cca",
        1066,
    ),
    (
        "model.safetensors",
        "65af59b1ff4450b09ecbf13ca35c840dbf038b26ff8e10e5ea89ca724828ed1e",
        557652046,
    ),
    (
        "special_tokens_map.json",
        "9463f61e1b109a8eb4688b829260d7c6b1e6dff04c98ff7269bb89e2b92369b9",
        286,
    ),
    (
        "spm.model",
        "13c8d666d62a7bc4ac8f040aab68e942c861f93303156cc28f5c7e885d86d6e3",
        4305025,
    ),
    (
        "tokenizer.json",
        "3aca3ce69a0a35aeb144a52c4f1d41c4246b8785f8f398315cc8fb6b24057810",
        16331396,
    ),
    (
        "tokenizer_config.json",
        "df9fd3a4482cc4a866788244824573fab8e74dadd1609d4f0e5997f9e96921dc",
        1256,
    ),
)


class NliDecision(StrEnum):
    """The only semantic decision exposed to the RAG layer."""

    ENTAILED = "ENTAILED"
    NOT_ENTAILED = "NOT_ENTAILED"


class NliError(RuntimeError):
    """Base class for fail-closed local NLI failures."""


class NliModelUnavailableError(NliError):
    """The pinned local model or its label contract is unavailable."""


class NliInputOverflowError(NliError):
    """The immutable pair exceeds the model's supported input length."""


class NliInferenceError(NliError):
    """The local classifier returned unusable inference output."""


@dataclass(frozen=True, slots=True)
class NliClassification:
    """Native label and diagnostic probabilities for one NLI pair."""

    decision: NliDecision
    native_label: str
    entailment_probability: float
    neutral_probability: float
    contradiction_probability: float


class _Tokenizer(Protocol):
    model_max_length: int

    def __call__(self, premise: str, hypothesis: str, **kwargs: object) -> Mapping[str, object]: ...


class _SequenceClassifier(Protocol):
    config: object

    def to(self, device: str) -> object: ...

    def eval(self) -> object: ...

    def __call__(self, **encoded: object) -> object: ...


TokenizerLoader = Callable[..., _Tokenizer]
ModelLoader = Callable[..., _SequenceClassifier]


def _load_tokenizer(**kwargs: object) -> _Tokenizer:
    from transformers import AutoTokenizer

    pretrained = kwargs.pop("pretrained", None)
    if not isinstance(pretrained, (str, Path)):
        raise NliModelUnavailableError("NLI snapshot identity is invalid")
    return cast(
        _Tokenizer,
        AutoTokenizer.from_pretrained(pretrained, **cast(dict[str, Any], kwargs)),
    )


def _load_model(**kwargs: object) -> _SequenceClassifier:
    from transformers import AutoModelForSequenceClassification

    pretrained = kwargs.pop("pretrained", None)
    if not isinstance(pretrained, (str, Path)):
        raise NliModelUnavailableError("NLI snapshot identity is invalid")
    return cast(
        _SequenceClassifier,
        AutoModelForSequenceClassification.from_pretrained(
            pretrained,
            **cast(dict[str, Any], kwargs),
        ),
    )


class PinnedCpuNliClassifier:
    """Lazily load and reuse the approved mDeBERTa snapshot on CPU only."""

    def __init__(
        self,
        *,
        snapshot_path: str | Path | None = None,
        repository: str = NLI_MODEL_REPOSITORY,
        revision: str = NLI_MODEL_REVISION,
        snapshot_manifest_sha256: str = NLI_SNAPSHOT_MANIFEST_SHA256,
        tokenizer_loader: TokenizerLoader = _load_tokenizer,
        model_loader: ModelLoader = _load_model,
    ) -> None:
        if repository != NLI_MODEL_REPOSITORY:
            raise ValueError("repository must be the approved NLI model")
        if revision != NLI_MODEL_REVISION:
            raise ValueError("revision must be the approved NLI snapshot")
        if snapshot_manifest_sha256 != NLI_SNAPSHOT_MANIFEST_SHA256:
            raise ValueError("snapshot manifest must be the approved NLI snapshot")
        if not callable(tokenizer_loader) or not callable(model_loader):
            raise ValueError("NLI loaders must be callable")
        self.repository = repository
        self.revision = revision
        self.snapshot_manifest_sha256 = snapshot_manifest_sha256
        self._snapshot_path = Path(snapshot_path) if snapshot_path is not None else None
        self._tokenizer_loader = tokenizer_loader
        self._model_loader = model_loader
        self._tokenizer: _Tokenizer | None = None
        self._model: _SequenceClassifier | None = None
        self._label_ids: dict[str, int] | None = None
        self._max_length: int | None = None
        self._closed = False
        self._load_lock = threading.Lock()

    @property
    def device(self) -> str:
        return "cpu"

    @property
    def model_loaded(self) -> bool:
        return self._model is not None

    def classify(self, premise: str, hypothesis: str) -> NliClassification:
        """Classify one trusted premise/hypothesis pair without truncation."""
        if self._closed:
            raise NliModelUnavailableError("NLI classifier is closed")
        if not isinstance(premise, str) or not premise.strip():
            raise ValueError("premise must be non-blank")
        if not isinstance(hypothesis, str) or not hypothesis.strip():
            raise ValueError("hypothesis must be non-blank")
        tokenizer, model = self._ensure_loaded()
        try:
            encoded = tokenizer(
                premise,
                hypothesis,
                return_tensors="pt",
                truncation=False,
                padding=False,
            )
            input_length = _input_length(encoded.get("input_ids"))
            max_length = self._max_length
            if max_length is None or input_length > max_length:
                raise NliInputOverflowError("NLI pair exceeds supported input length")
            import torch

            with torch.inference_mode():
                output = model(**dict(encoded))
            logits = getattr(output, "logits", None)
            values = _logit_values(logits)
            probabilities = torch.softmax(torch.tensor(values, dtype=torch.float32), dim=0)
            native_index = int(torch.argmax(probabilities).item())
            labels = self._label_ids
            if labels is None:
                raise NliModelUnavailableError("NLI labels are unavailable")
            native_label = next(
                label for label, label_id in labels.items() if label_id == native_index
            )
            return NliClassification(
                NliDecision.ENTAILED if native_label == "entailment" else NliDecision.NOT_ENTAILED,
                native_label,
                float(probabilities[labels["entailment"]].item()),
                float(probabilities[labels["neutral"]].item()),
                float(probabilities[labels["contradiction"]].item()),
            )
        except (NliInputOverflowError, NliModelUnavailableError):
            raise
        except Exception as error:
            raise NliInferenceError("NLI inference failed") from error

    def close(self) -> None:
        """Release model/tokenizer resources; safe to call repeatedly."""
        if self._closed:
            return
        self._closed = True
        self._model = None
        self._tokenizer = None
        self._label_ids = None
        self._max_length = None
        gc.collect()

    def warm_up(self) -> None:
        """Load the model ahead of the first classification (safe to repeat)."""
        self._ensure_loaded()

    def _ensure_loaded(self) -> tuple[_Tokenizer, _SequenceClassifier]:
        if self._model is not None and self._tokenizer is not None:
            return self._tokenizer, self._model
        # A background warm-up and a request may race to load the model.
        with self._load_lock:
            if self._model is not None and self._tokenizer is not None:
                return self._tokenizer, self._model
            return self._load()

    def _load(self) -> tuple[_Tokenizer, _SequenceClassifier]:
        pretrained: str
        kwargs: dict[str, object] = {
            "revision": self.revision,
            "local_files_only": True,
        }
        if self._snapshot_path is not None:
            _verify_snapshot_manifest(self._snapshot_path, self.repository, self.revision)
            pretrained = str(self._snapshot_path)
            kwargs = {"local_files_only": True}
        else:
            pretrained = self.repository
        try:
            tokenizer = self._tokenizer_loader(pretrained=pretrained, **kwargs)
            model = self._model_loader(pretrained=pretrained, **kwargs)
            model.to("cpu")
            model.eval()
            label_ids = _resolve_label_ids(getattr(model, "config", None))
            max_length = _resolve_max_length(tokenizer, getattr(model, "config", None))
        except NliModelUnavailableError:
            raise
        except Exception as error:
            raise NliModelUnavailableError("approved local NLI snapshot is unavailable") from error
        self._tokenizer, self._model = tokenizer, model
        self._label_ids, self._max_length = label_ids, max_length
        return tokenizer, model


def _verify_snapshot_manifest(path: Path, repository: str, revision: str) -> None:
    """Require the exact reviewed local snapshot before loading model code."""
    if not path.is_dir():
        raise NliModelUnavailableError("approved local NLI snapshot is unavailable")
    expected_names = {name for name, _, _ in _SNAPSHOT_FILES}
    try:
        actual_names = {entry.name for entry in path.iterdir() if entry.is_file()}
    except OSError as error:
        raise NliModelUnavailableError("approved local NLI snapshot is unavailable") from error
    if actual_names != expected_names:
        raise NliModelUnavailableError("approved local NLI snapshot manifest mismatch")
    records: list[dict[str, object]] = []
    try:
        for name, expected_hash, expected_size in _SNAPSHOT_FILES:
            content = (path / name).read_bytes()
            actual_hash = sha256(content).hexdigest()
            if len(content) != expected_size or actual_hash != expected_hash:
                raise NliModelUnavailableError("approved local NLI snapshot manifest mismatch")
            records.append({"name": name, "sha256": actual_hash, "size": len(content)})
    except OSError as error:
        raise NliModelUnavailableError("approved local NLI snapshot is unavailable") from error
    manifest = sha256(
        json.dumps(
            {"repo": repository, "commit": revision, "files": records},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if manifest != NLI_SNAPSHOT_MANIFEST_SHA256:
        raise NliModelUnavailableError("approved local NLI snapshot manifest mismatch")


def _resolve_label_ids(config: object) -> dict[str, int]:
    id2label = getattr(config, "id2label", None)
    label2id = getattr(config, "label2id", None)
    if not isinstance(id2label, Mapping) or not isinstance(label2id, Mapping):
        raise NliModelUnavailableError("NLI labels are missing")
    resolved: dict[str, int] = {}
    for raw_id, raw_label in id2label.items():
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            raise NliModelUnavailableError("NLI labels are invalid")
        if not isinstance(raw_label, str):
            raise NliModelUnavailableError("NLI labels are invalid")
        label = raw_label.strip().casefold()
        if label not in {"entailment", "neutral", "contradiction"} or label in resolved:
            raise NliModelUnavailableError("NLI labels are ambiguous")
        resolved[label] = raw_id
    if set(resolved) != {"entailment", "neutral", "contradiction"}:
        raise NliModelUnavailableError("NLI labels are incomplete")
    if set(resolved.values()) != {0, 1, 2}:
        raise NliModelUnavailableError("NLI label IDs are incompatible")
    for raw_label, raw_id in label2id.items():
        if (
            not isinstance(raw_label, str)
            or not isinstance(raw_id, int)
            or isinstance(raw_id, bool)
        ):
            raise NliModelUnavailableError("NLI labels are invalid")
        normalized = raw_label.strip().casefold()
        if normalized not in resolved or resolved[normalized] != raw_id:
            raise NliModelUnavailableError("NLI labels disagree")
    if len(label2id) != 3:
        raise NliModelUnavailableError("NLI labels are ambiguous")
    return resolved


def _resolve_max_length(tokenizer: object, config: object) -> int:
    candidates: list[int] = []
    for owner in (tokenizer, config):
        value = getattr(owner, "model_max_length", None)
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 1_000_000:
            candidates.append(value)
        value = getattr(owner, "max_position_embeddings", None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            candidates.append(value)
    if not candidates:
        raise NliModelUnavailableError("NLI maximum input length is unavailable")
    return min(candidates)


def _input_length(value: object) -> int:
    shape = getattr(value, "shape", None)
    if shape is not None and len(shape) == 2:
        return int(shape[1])
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
        return len(value[0])
    raise NliInferenceError("NLI tokenizer output is invalid")


def _logit_values(value: object) -> tuple[float, ...]:
    tolist = getattr(value, "detach", None)
    if callable(tolist):
        value = tolist().cpu().tolist()
    if isinstance(value, list) and value and isinstance(value[0], list):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise NliInferenceError("NLI logits are invalid")
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        raise NliInferenceError("NLI logits are invalid")
    return tuple(float(item) for item in value)


__all__ = [
    "NLI_MODEL_REPOSITORY",
    "NLI_MODEL_REVISION",
    "NLI_SNAPSHOT_MANIFEST_SHA256",
    "NliClassification",
    "NliDecision",
    "NliError",
    "NliInferenceError",
    "NliInputOverflowError",
    "NliModelUnavailableError",
    "PinnedCpuNliClassifier",
]
