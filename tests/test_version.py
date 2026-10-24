"""The version string has one source and is what --version prints."""

import sys
from pathlib import Path

from main import CouchDBSQL, __version__, main


def test_version_flag_prints_the_package_version(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--version"])
    main()
    assert capsys.readouterr().out == f"couchdb_sql {__version__}\n"
    assert __version__ == "0.2.0"


def test_startup_banner_includes_the_version():
    assert __version__ in CouchDBSQL.intro
    changelog = Path(__file__).resolve().parents[1].joinpath("CHANGELOG.md").read_text()
    assert f"## [{__version__}] - 2026-10-24" in changelog
