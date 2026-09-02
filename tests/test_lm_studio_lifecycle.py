"""Deterministic LM Studio model-residency lifecycle tests."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from aaos.inference.lm_studio_lifecycle import (
    APPROVED_QWEN_MODEL,
    LmStudioModelLifecycle,
    ModelLifecycleError,
)


@dataclass
class _Result:
    returncode: int
    stdout: str | None


class _Runner:
    def __init__(self, inventories: list[list[dict[str, object]]]) -> None:
        self.inventories = inventories
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> _Result:
        self.commands.append(argv)
        if argv[1:] == ("ps", "--json"):
            if not self.inventories:
                raise AssertionError("unexpected inventory query")
            return _Result(0, json.dumps(self.inventories.pop(0)))
        return _Result(0, "")


def _loaded(*, identifier: str = APPROVED_QWEN_MODEL) -> dict[str, object]:
    return {
        "identifier": identifier,
        "modelKey": identifier,
        "contextLength": 8192,
        "status": "idle",
    }


def test_unload_is_verified_before_bge_acquisition() -> None:
    runner = _Runner([[_loaded()], []])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    lifecycle.ensure_answer_model_unloaded()

    assert runner.commands == [
        ("lms", "ps", "--json"),
        ("lms", "unload", APPROVED_QWEN_MODEL),
        ("lms", "ps", "--json"),
    ]


def test_already_unloaded_does_not_issue_unload() -> None:
    runner = _Runner([[]])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    lifecycle.ensure_answer_model_unloaded()

    assert runner.commands == [("lms", "ps", "--json")]


def test_load_verifies_exact_identity_and_context() -> None:
    runner = _Runner([[], [_loaded()]])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    lifecycle.ensure_answer_model_loaded()

    assert runner.commands == [
        ("lms", "ps", "--json"),
        ("lms", "load", APPROVED_QWEN_MODEL, "--gpu", "max", "--context-length", "8192", "-y"),
        ("lms", "ps", "--json"),
    ]


def test_wrong_identity_fails_closed() -> None:
    runner = _Runner([[_loaded(identifier="other/model")]])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    with pytest.raises(ModelLifecycleError):
        lifecycle.ensure_answer_model_loaded()


def test_unload_failure_prevents_progression() -> None:
    class _FailingRunner(_Runner):
        def __call__(self, argv: tuple[str, ...]) -> _Result:
            self.commands.append(argv)
            if argv[1:] == ("ps", "--json"):
                return _Result(0, json.dumps([_loaded()]))
            return _Result(1, "private failure")

    runner = _FailingRunner([])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    with pytest.raises(ModelLifecycleError):
        lifecycle.ensure_answer_model_unloaded()


def test_timeout_is_fixed_and_fail_closed() -> None:
    class _TimeoutRunner:
        def __call__(self, _argv: tuple[str, ...]) -> _Result:
            raise TimeoutError("private timeout")

    lifecycle = LmStudioModelLifecycle(command_runner=_TimeoutRunner(), executable="lms")

    with pytest.raises(ModelLifecycleError):
        lifecycle.ensure_answer_model_unloaded()


def test_no_all_unload_and_no_untrusted_command_fragments() -> None:
    runner = _Runner([[]])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    lifecycle.ensure_answer_model_unloaded()

    assert all("--all" not in argument for command in runner.commands for argument in command)
    assert all("private" not in argument for command in runner.commands for argument in command)


def test_malformed_inventory_fails_closed() -> None:
    runner = _Runner([])
    runner.inventories = [[{"identifier": APPROVED_QWEN_MODEL, "modelKey": "other"}]]
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    with pytest.raises(ModelLifecycleError):
        lifecycle.ensure_answer_model_unloaded()


def test_close_is_idempotent_and_unloads_after_use() -> None:
    runner = _Runner([[], [_loaded()], [_loaded()], []])
    lifecycle = LmStudioModelLifecycle(command_runner=runner, executable="lms")

    lifecycle.ensure_answer_model_loaded()
    lifecycle.close()
    lifecycle.close()

    assert runner.commands[-3:] == [
        ("lms", "ps", "--json"),
        ("lms", "unload", APPROVED_QWEN_MODEL),
        ("lms", "ps", "--json"),
    ]
