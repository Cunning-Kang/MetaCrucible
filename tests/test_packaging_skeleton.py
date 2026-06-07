"""Tests for Issue #2: packaging/entrypoint skeleton.

These tests verify the packaging and installable entrypoint asserted by
Issue #2:

  - ``pyproject.toml`` exists at the repo root and is valid TOML.
  - A PEP 517 build backend is declared so a wheel can be produced.
  - Project metadata is populated for later PyPI work (name, version,
    description, readme, requires-python, license, authors).
  - A ``metacrucible`` console script is declared in ``[project.scripts]``
    and the target ``module:attr`` is importable.
  - The ``src/metacrucible/`` package layout exists with ``__init__.py``.
  - The console script runs successfully and reports the package version.
  - A wheel can be produced by the build backend (the build dry-run).
"""
from __future__ import annotations

import importlib
import importlib.util
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SRC_DIR = REPO_ROOT / "src" / "metacrucible"
EXPECTED_VERSION = "0.1.0"


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _load_pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _project_meta() -> dict:
    return _load_pyproject().get("project", {})


def _console_entry() -> str:
    return _project_meta().get("scripts", {}).get("metacrucible", "")


def _resolve_console_target(entry: str) -> tuple[str, str]:
    """Split a ``module:attr`` console entry into ``(module, attr)``."""
    module, _, attr = entry.partition(":")
    if not module or not attr:
        raise ValueError(f"console entry must be 'module:attr'; got {entry!r}")
    return module, attr


# --------------------------------------------------------------------------- #
# pyproject.toml — file + PEP 517 build system                                #
# --------------------------------------------------------------------------- #


def test_pyproject_toml_exists_at_repo_root() -> None:
    assert PYPROJECT.is_file(), f"expected {PYPROJECT.relative_to(REPO_ROOT)} to exist"


def test_pyproject_toml_is_valid_toml() -> None:
    # Raises tomllib.TOMLDecodeError on malformed input.
    _load_pyproject()


def test_pyproject_declares_pep517_build_system() -> None:
    data = _load_pyproject()
    bs = data.get("build-system")
    assert isinstance(bs, dict), "pyproject.toml must declare a [build-system] table"
    backend = bs.get("build-backend")
    assert isinstance(backend, str) and backend, (
        "[build-system].build-backend must be a non-empty string"
    )
    requires = bs.get("requires")
    assert isinstance(requires, list) and requires, (
        "[build-system].requires must be a non-empty list of backend packages"
    )
    for req in requires:
        assert isinstance(req, str) and req.strip(), (
            f"each [build-system].requires entry must be a non-empty string; got {req!r}"
        )


# --------------------------------------------------------------------------- #
# Project metadata — PyPI readiness                                           #
# --------------------------------------------------------------------------- #


def test_project_name_is_metacrucible() -> None:
    assert _project_meta().get("name") == "metacrucible", (
        f"project.name must be 'metacrucible'; got {_project_meta().get('name')!r}"
    )


def test_project_version_is_semver() -> None:
    version = _project_meta().get("version")
    assert isinstance(version, str) and version, "project.version must be set"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"project.version should be a SemVer 'MAJOR.MINOR.PATCH' string; got {version!r}"
    )


def test_project_version_matches_init_dunder() -> None:
    """The package's ``__version__`` string must match ``project.version``.

    Keeping the two in sync prevents drift between the runtime package
    and the build metadata that the wheel advertises.
    """
    pkg_version = importlib.import_module("metacrucible").__version__
    assert pkg_version == _project_meta().get("version") == EXPECTED_VERSION, (
        f"metacrucible.__version__ ({pkg_version!r}) must match "
        f"project.version ({_project_meta().get('version')!r})"
    )


def test_project_description_is_nonempty() -> None:
    desc = (_project_meta().get("description") or "").strip()
    assert desc, "project.description must be a non-empty string"


def test_project_readme_points_to_existing_file() -> None:
    readme = _project_meta().get("readme")
    assert readme, "project.readme must be set to a README path or table"
    # String form: relative path under the repo root.
    if isinstance(readme, str):
        readme_path = REPO_ROOT / readme
        assert readme_path.is_file(), (
            f"project.readme={readme!r} must point to an existing file; "
            f"missing {readme_path.relative_to(REPO_ROOT)}"
        )
    else:
        # Table form: must have a 'file' or 'text' key per PEP 621.
        assert "file" in readme or "text" in readme, (
            f"project.readme table must have 'file' or 'text'; got {readme!r}"
        )


def test_project_requires_python_pins_3_14_plus() -> None:
    rp = _project_meta().get("requires-python")
    assert isinstance(rp, str) and rp, "project.requires-python must be set"
    match = re.search(r"3\.(\d+)", rp)
    assert match, f"project.requires-python must pin a Python 3.x range; got {rp!r}"
    assert int(match.group(1)) >= 14, (
        f"project.requires-python must require Python >= 3.14 to match mise.toml; "
        f"got {rp!r}"
    )


def test_project_license_is_mit() -> None:
    """ADR 0036 pins MIT as the intended project license."""
    assert _project_meta().get("license") == "MIT", (
        f"project.license must be 'MIT' (per ADR 0036); "
        f"got {_project_meta().get('license')!r}"
    )


def test_project_lists_at_least_one_author() -> None:
    authors = _project_meta().get("authors")
    assert isinstance(authors, list) and authors, (
        "project.authors must be a non-empty list"
    )
    for author in authors:
        assert isinstance(author, dict) and (author.get("name") or author.get("email")), (
            f"each author entry must have at least a 'name' or 'email'; got {author!r}"
        )


def test_project_classifiers_include_mit_and_python_3_14() -> None:
    classifiers = _project_meta().get("classifiers", [])
    assert isinstance(classifiers, list) and classifiers, (
        "project.classifiers must list at least the license + Python version"
    )
    joined = "\n".join(classifiers)
    assert "MIT License" in joined, (
        f"project.classifiers should include an MIT License classifier; got {classifiers!r}"
    )
    assert "Python :: 3.14" in joined, (
        f"project.classifiers should include a 'Python :: 3.14' classifier; "
        f"got {classifiers!r}"
    )


# --------------------------------------------------------------------------- #
# Console script entrypoint                                                   #
# --------------------------------------------------------------------------- #


def test_project_console_script_metacrucible_declared() -> None:
    entry = _console_entry()
    assert entry, "project.scripts must define a 'metacrucible' console entry"


def test_console_script_target_is_importable() -> None:
    module, attr = _resolve_console_target(_console_entry())
    mod = importlib.import_module(module)
    assert hasattr(mod, attr), (
        f"console-script target {module!r} has no attribute {attr!r}"
    )


def test_console_script_callable_is_invokable() -> None:
    """The console-script target must be a callable that returns an int exit code."""
    module, attr = _resolve_console_target(_console_entry())
    fn = getattr(importlib.import_module(module), attr)
    assert callable(fn), f"{module}:{attr} must be callable"
    rc = fn(["--version"])
    assert rc == 0, f"{module}:{attr}(['--version']) must return 0; got {rc!r}"


# --------------------------------------------------------------------------- #
# Package layout                                                              #
# --------------------------------------------------------------------------- #


def test_src_layout_package_directory_exists() -> None:
    assert SRC_DIR.is_dir(), f"expected {SRC_DIR.relative_to(REPO_ROOT)}/"
    assert (SRC_DIR / "__init__.py").is_file(), (
        f"expected {SRC_DIR.relative_to(REPO_ROOT)}/__init__.py"
    )


def test_package_importable_after_install() -> None:
    """The ``metacrucible`` package must be importable on ``sys.path``."""
    spec = importlib.util.find_spec("metacrucible")
    assert spec is not None, "metacrucible package is not importable on sys.path"
    assert spec.origin and "metacrucible/__init__.py" in str(spec.origin), (
        f"metacrucible package origin looks wrong; got {spec.origin!r}"
    )


# --------------------------------------------------------------------------- #
# Console script — end-to-end invocation                                      #
# --------------------------------------------------------------------------- #


def test_console_script_subprocess_reports_version() -> None:
    """``python -m metacrucible --version`` must exit 0 and print the version."""
    result = subprocess.run(
        [sys.executable, "-m", "metacrucible", "--version"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, (
        f"`python -m metacrucible --version` failed "
        f"(rc={result.returncode}):\nstdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert EXPECTED_VERSION in result.stdout, (
        f"--version output must include {EXPECTED_VERSION!r}; got {result.stdout!r}"
    )


# --------------------------------------------------------------------------- #
# Wheel build — the "dry-run" of the PEP 517 backend                          #
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="uv is not on PATH; the wheel build dry-run needs uv as the build orchestrator",
)
def test_wheel_build_succeeds(tmp_path: Path) -> None:
    """``uv build --wheel`` must produce a ``metacrucible-*.whl`` artifact.

    This is the PEP 517 build dry-run: it exercises the declared build
    backend end-to-end against the project source, without publishing.
    The wheel filename encodes the project name and version, so its
    presence confirms both metadata and build worked.
    """
    result = subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`uv build --wheel` failed (rc={result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    wheels = sorted(tmp_path.glob("metacrucible-*.whl"))
    assert wheels, (
        f"expected a metacrucible-*.whl in {tmp_path}; "
        f"got artifacts: {sorted(p.name for p in tmp_path.iterdir())}"
    )
    # Filename pattern: metacrucible-0.1.0-py3-none-any.whl
    expected = f"metacrucible-{EXPECTED_VERSION}-py3-none-any.whl"
    assert any(w.name == expected for w in wheels), (
        f"expected wheel named {expected!r}; got {[w.name for w in wheels]}"
    )
