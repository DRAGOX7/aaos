"""Bounded, fixed-command LM Studio model residency control."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from .telemetry import (
    LocalInferenceTelemetryEvent,
    TelemetryOperation,
    TelemetrySink,
    emit_local_inference_telemetry,
    safe_backend_category,
    safe_error_code,
    safe_exception_type,
)

APPROVED_QWEN_MODEL = "qwen/qwen3.5-4b"
DEFAULT_LM_STUDIO_CONTEXT_LENGTH = 8192
MODEL_LIFECYCLE_COMMAND_TIMEOUT_SECONDS = 120.0


class ModelLifecycleError(RuntimeError):
    """A fixed local model lifecycle failure with no backend detail."""

    def __init__(self) -> None:
        super().__init__("local model lifecycle failed")


class ModelLifecycle(Protocol):
    """Narrow deterministic residency boundary used by Phase 4 composition."""

    def ensure_answer_model_unloaded(self) -> None: ...

    def ensure_answer_model_loaded(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _LoadedModel:
    identifier: str
    context_length: int | None


class _CommandResult(Protocol):
    returncode: int
    stdout: str | None


CommandRunner = Callable[[tuple[str, ...]], _CommandResult]


class LmStudioModelLifecycle:
    """Own exact Qwen load/unload commands without a generic tool surface.

    The executable is resolved lazily so construction never starts a process.
    Tests inject ``command_runner`` and therefore never invoke LM Studio.
    """

    def __init__(
        self,
        *,
        model_identifier: str = APPROVED_QWEN_MODEL,
        context_length: int = DEFAULT_LM_STUDIO_CONTEXT_LENGTH,
        executable: str | None = None,
        command_runner: CommandRunner | None = None,
        timeout_seconds: float = MODEL_LIFECYCLE_COMMAND_TIMEOUT_SECONDS,
        diagnostic_sink: TelemetrySink | None = None,
    ) -> None:
        if model_identifier != APPROVED_QWEN_MODEL:
            raise ValueError("only the approved Qwen model is supported")
        if (
            isinstance(context_length, bool)
            or not isinstance(context_length, int)
            or context_length <= 0
        ):
            raise ValueError("context_length must be positive")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive")
        if executable is not None and not isinstance(executable, str):
            raise ValueError("executable must be a string")
        self._model_identifier = model_identifier
        self._context_length = context_length
        self._executable = executable
        self._command_runner: CommandRunner = (
            command_runner if command_runner is not None else self._run_command
        )
        self._timeout_seconds = float(timeout_seconds)
        self._diagnostic_sink = diagnostic_sink or emit_local_inference_telemetry
        self._used = False
        self._closed = False

    def ensure_answer_model_unloaded(self) -> None:
        """Ensure the exact answer model is absent before embedding work."""
        self._require_open()
        self._used = True
        loaded: _LoadedModel | None = None
        try:
            loaded = self._find_answer_model(self._inventory())
            if loaded is None:
                self._emit(
                    LocalInferenceTelemetryEvent(
                        operation="UNLOAD",
                        model_identifier=self._model_identifier,
                        model_role="answer",
                        lifecycle_state="completed",
                        model_listed_before=False,
                        model_listed_after=False,
                        context_length=self._context_length,
                    )
                )
                return
            self._run(("unload", self._model_identifier), operation="UNLOAD")
            remaining = self._find_answer_model(self._inventory())
            if remaining is not None:
                raise ModelLifecycleError()
            self._emit(
                LocalInferenceTelemetryEvent(
                    operation="UNLOAD",
                    model_identifier=self._model_identifier,
                    model_role="answer",
                    lifecycle_state="completed",
                    model_listed_before=True,
                    model_listed_after=False,
                    context_length=self._context_length,
                )
            )
        except Exception as error:
            self._emit_failure("UNLOAD", error, model_listed_before=loaded is not None)
            raise

    def ensure_answer_model_loaded(self) -> None:
        """Ensure the exact answer model is loaded with the approved context."""
        self._require_open()
        self._used = True
        loaded: _LoadedModel | None = None
        try:
            loaded = self._find_answer_model(self._inventory())
            if loaded is not None:
                self._validate_context(loaded)
                self._emit(
                    LocalInferenceTelemetryEvent(
                        operation="LOAD",
                        model_identifier=self._model_identifier,
                        model_role="answer",
                        lifecycle_state="completed",
                        model_listed_before=True,
                        model_listed_after=True,
                        context_length=self._context_length,
                    )
                )
                return
            self._run(
                (
                    "load",
                    self._model_identifier,
                    "--gpu",
                    "max",
                    "--context-length",
                    str(self._context_length),
                    "-y",
                ),
                operation="LOAD",
            )
            loaded = self._find_answer_model(self._inventory())
            if loaded is None:
                raise ModelLifecycleError()
            self._validate_context(loaded)
            self._emit(
                LocalInferenceTelemetryEvent(
                    operation="LOAD",
                    model_identifier=self._model_identifier,
                    model_role="answer",
                    lifecycle_state="completed",
                    model_listed_before=False,
                    model_listed_after=True,
                    context_length=self._context_length,
                )
            )
        except Exception as error:
            self._emit_failure("LOAD", error, model_listed_before=loaded is not None)
            raise

    def close(self) -> None:
        """Unload the owned answer model once, without process termination reliance."""
        if self._closed:
            self._emit(
                LocalInferenceTelemetryEvent(
                    operation="FINAL_CLEANUP",
                    model_identifier=self._model_identifier,
                    model_role="answer",
                    lifecycle_state="already_completed",
                    cleanup_already_completed=True,
                    context_length=self._context_length,
                )
            )
            return
        self._closed = True
        if not self._used:
            self._emit(
                LocalInferenceTelemetryEvent(
                    operation="FINAL_CLEANUP",
                    model_identifier=self._model_identifier,
                    model_role="answer",
                    lifecycle_state="skipped",
                    cleanup_already_completed=False,
                    context_length=self._context_length,
                )
            )
            return
        loaded_before = False
        try:
            loaded = self._find_answer_model(self._inventory())
            loaded_before = loaded is not None
            if loaded is not None:
                self._run(("unload", self._model_identifier), operation="UNLOAD")
                if self._find_answer_model(self._inventory()) is not None:
                    raise ModelLifecycleError()
            self._emit(
                LocalInferenceTelemetryEvent(
                    operation="FINAL_CLEANUP",
                    model_identifier=self._model_identifier,
                    model_role="answer",
                    lifecycle_state="completed",
                    cleanup_already_completed=False,
                    model_listed_before=loaded_before,
                    model_listed_after=False,
                    context_length=self._context_length,
                )
            )
        except ModelLifecycleError:
            self._emit_failure(
                "FINAL_CLEANUP",
                ModelLifecycleError(),
                model_listed_before=loaded_before,
            )
            raise
        except Exception as error:
            self._emit_failure(
                "FINAL_CLEANUP",
                error,
                model_listed_before=loaded_before,
            )
            raise ModelLifecycleError() from None

    def _inventory(self) -> tuple[_LoadedModel, ...]:
        try:
            result = self._run(("ps", "--json"), operation="READY_CHECK")
            raw = result.stdout
            if not isinstance(raw, str):
                raise ModelLifecycleError()
            try:
                payload: object = json.loads(raw)
            except (TypeError, ValueError):
                raise ModelLifecycleError() from None
            if not isinstance(payload, list):
                raise ModelLifecycleError()
            models: list[_LoadedModel] = []
            for item in payload:
                models.append(_parse_loaded_model(item))
            return tuple(models)
        except ModelLifecycleError as error:
            self._emit_failure("READY_CHECK", error)
            raise

    def _find_answer_model(self, models: tuple[_LoadedModel, ...]) -> _LoadedModel | None:
        matches = [model for model in models if model.identifier == self._model_identifier]
        if len(matches) > 1:
            raise ModelLifecycleError()
        return matches[0] if matches else None

    def _validate_context(self, loaded: _LoadedModel) -> None:
        if loaded.context_length is not None and loaded.context_length != self._context_length:
            raise ModelLifecycleError()

    def _run(self, arguments: tuple[str, ...], *, operation: TelemetryOperation) -> _CommandResult:
        try:
            executable = self._resolve_executable()
            argv = (executable, *arguments)
            result = self._command_runner(argv)
        except Exception as error:
            self._emit_failure(operation, error)
            raise ModelLifecycleError() from None
        if not isinstance(result.returncode, int) or result.returncode != 0:
            self._emit_failure(
                operation,
                ModelLifecycleError(),
                backend_category="command_failure",
            )
            raise ModelLifecycleError()
        return result

    def _emit_failure(
        self,
        operation: TelemetryOperation,
        error: BaseException,
        *,
        model_listed_before: bool | None = None,
        backend_category: str | None = None,
    ) -> None:
        self._emit(
            LocalInferenceTelemetryEvent(
                operation=operation,
                model_identifier=self._model_identifier,
                model_role="answer",
                lifecycle_state="failed",
                exception_type=safe_exception_type(error),
                safe_error_code=safe_error_code(error),
                backend_category=backend_category or safe_backend_category(error),
                model_listed_before=model_listed_before,
                context_length=self._context_length,
            )
        )

    def _emit(self, event: LocalInferenceTelemetryEvent) -> None:
        try:
            self._diagnostic_sink(event)
        except Exception:
            return

    def _resolve_executable(self) -> str:
        executable = self._executable or shutil.which("lms")
        if not isinstance(executable, str) or not executable:
            raise ModelLifecycleError()
        return executable

    def _run_command(self, argv: tuple[str, ...]) -> _CommandResult:
        return cast(
            _CommandResult,
            subprocess.run(
                list(argv),
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                timeout=self._timeout_seconds,
            ),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise ModelLifecycleError()


def _parse_loaded_model(value: object) -> _LoadedModel:
    if not isinstance(value, dict):
        raise ModelLifecycleError()
    identifier = value.get("identifier")
    model_key = value.get("modelKey")
    if identifier is not None and (not isinstance(identifier, str) or not identifier.strip()):
        raise ModelLifecycleError()
    if model_key is not None and (not isinstance(model_key, str) or not model_key.strip()):
        raise ModelLifecycleError()
    if identifier is None and model_key is None:
        raise ModelLifecycleError()
    if identifier is not None and model_key is not None and identifier != model_key:
        raise ModelLifecycleError()
    selected = cast(str, identifier if identifier is not None else model_key)
    context_length = value.get("contextLength")
    if context_length is not None and (
        not isinstance(context_length, int)
        or isinstance(context_length, bool)
        or context_length <= 0
    ):
        raise ModelLifecycleError()
    return _LoadedModel(selected, context_length)


__all__ = [
    "APPROVED_QWEN_MODEL",
    "DEFAULT_LM_STUDIO_CONTEXT_LENGTH",
    "LmStudioModelLifecycle",
    "ModelLifecycle",
    "ModelLifecycleError",
    "MODEL_LIFECYCLE_COMMAND_TIMEOUT_SECONDS",
]
