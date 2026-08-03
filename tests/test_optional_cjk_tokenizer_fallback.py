"""Regression tests for SQLite builds without loadable-extension support."""

import sqlite3
from pathlib import Path

import hermes_state
from hermes_state import SessionDB


class _ConnectionWithoutLoadableExtensions:
    def enable_load_extension(self, enabled):
        raise AttributeError(
            "'sqlite3.Connection' object has no attribute 'enable_load_extension'"
        )


def test_missing_extension_api_degrades_without_cleanup_error(tmp_path, monkeypatch):
    """A CPython build without loadable extensions must not crash SessionDB init."""
    db = object.__new__(SessionDB)
    db.__dict__["_conn"] = _ConnectionWithoutLoadableExtensions()
    monkeypatch.setattr(
        db,
        "_get_libsimple_path",
        lambda: Path(tmp_path) / "libsimple.so",
    )
    (tmp_path / "libsimple.so").touch()

    assert db._load_simple_extension() is False


def test_simple_tokenizer_missing_is_optional_fts5_failure():
    """The simple tokenizer failure disables only the optional CJK FTS5 path."""
    error = sqlite3.OperationalError("no such tokenizer: simple")

    assert SessionDB._is_trigram_unavailable_error(error) is True
    assert SessionDB._is_fts5_unavailable_error(error) is True
