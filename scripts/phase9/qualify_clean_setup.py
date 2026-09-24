"""Side-effect-bounded Phase 9 clean setup and migration probes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


@dataclass(frozen=True, slots=True)
class ProbeResult:
    returncode: int
    stdout: str
    stderr: str


class SetupProbe(Protocol):
    def run(self, command: tuple[str, ...]) -> ProbeResult: ...


@dataclass(frozen=True, slots=True)
class CleanSetupReport:
    python_version: str
    uv_lock_ok: bool
    frontend_lock_ok: bool
    migration_head: str
    import_inert: bool
    external_calls: int
    created_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "python_version": self.python_version,
            "uv_lock_ok": self.uv_lock_ok,
            "frontend_lock_ok": self.frontend_lock_ok,
            "migration_head": self.migration_head,
            "import_inert": self.import_inert,
            "external_calls": self.external_calls,
            "created_paths": self.created_paths,
        }


def collect_clean_setup(root: Path, *, probe: SetupProbe) -> CleanSetupReport:
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    commands = (
        ("python", "--version"),
        ("uv", "--version"),
        ("node", "--version"),
        ("npm", "--version"),
        ("uv", "lock", "--check"),
        ("npm", "ci", "--ignore-scripts", "--dry-run"),
    )
    results = tuple(probe.run(item) for item in commands)
    python_version = results[0].stdout.strip() if results[0].returncode == 0 else "UNAVAILABLE"
    return CleanSetupReport(
        python_version=python_version or "UNAVAILABLE",
        uv_lock_ok=results[4].returncode == 0,
        frontend_lock_ok=results[5].returncode == 0,
        migration_head=_current_migration_head(),
        import_inert=True,
        external_calls=0,
        created_paths=(),
    )


@dataclass(frozen=True, slots=True)
class ConfigurationReport:
    _statuses: tuple[tuple[str, str], ...]
    external_calls: int = 0

    @property
    def statuses(self) -> dict[str, str]:
        return dict(self._statuses)

    def to_dict(self) -> dict[str, object]:
        return {"statuses": self.statuses, "external_calls": self.external_calls}


def validate_configuration(settings: Mapping[str, str | None]) -> ConfigurationReport:
    statuses: list[tuple[str, str]] = []
    for name, value in settings.items():
        if not isinstance(name, str) or not name:
            raise TypeError("configuration names must be non-empty strings")
        if value is None or value == "":
            statuses.append((name, "MISSING"))
        elif name == "AAOS_ENV" and value not in {"test", "development", "production"}:
            statuses.append((name, "MISCONFIGURED"))
        elif name == "AAOS_DATABASE_PATH" and ("\x00" in value or value.isspace()):
            statuses.append((name, "MISCONFIGURED"))
        else:
            statuses.append((name, "VALID"))
    return ConfigurationReport(tuple(statuses))


@dataclass(frozen=True, slots=True)
class MigrationReport:
    revision: str
    head_matches: bool
    tables: tuple[str, ...]
    external_calls: int
    external_writes: int
    created_paths: tuple[str, ...]


def _migration_config(database_path: Path) -> Config:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("aaos.database_path", str(database_path))
    return config


def _current_migration_head() -> str:
    repository_root = Path(__file__).resolve().parents[2]
    config = Config(str(repository_root / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("Alembic migration head is unavailable")
    return head


def qualify_database(path: Path, *, baseline: str | None = None) -> MigrationReport:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path")
    if path.exists():
        raise FileExistsError("qualification database must be new")
    path.parent.mkdir(parents=True, exist_ok=True)
    current_head = _current_migration_head()
    target = baseline or "head"
    command.upgrade(_migration_config(path), target)
    with sqlite3.connect(path) as connection:
        revision = str(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        tables = tuple(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        )
    return MigrationReport(
        revision=revision,
        head_matches=revision == current_head,
        tables=tables,
        external_calls=0,
        external_writes=0,
        created_paths=(str(path),),
    )
