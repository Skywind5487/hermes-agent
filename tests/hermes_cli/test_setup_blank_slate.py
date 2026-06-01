"""Tests for Blank Slate setup mode (hermes_cli/setup.py).

Blank Slate is the third first-time setup option: everything off except the
bare minimum needed to run an agent (provider/model + file + terminal). These
tests pin the config the writers produce and the invariant that the toolset
resolver + tool-schema builder yield exactly the file/terminal tools.
"""

import pytest

from hermes_cli.setup import (
    _blank_slate_minimal_toolsets,
    _blank_slate_minimize_config,
)


class TestBlankSlateMinimalToolsets:
    def test_only_file_and_terminal_enabled_for_cli(self):
        cfg = {}
        _blank_slate_minimal_toolsets(cfg)
        assert cfg["platform_toolsets"]["cli"] == ["file", "terminal"]

    def test_disabled_toolsets_excludes_kept_and_covers_known(self):
        cfg = {}
        _blank_slate_minimal_toolsets(cfg)
        disabled = set(cfg["agent"]["disabled_toolsets"])
        # The two kept toolsets must NOT be in the disabled list.
        assert "file" not in disabled
        assert "terminal" not in disabled
        # A representative spread of capabilities must be suppressed.
        for ts in ("web", "browser", "code_execution", "vision", "memory",
                   "delegation", "cronjob", "skills", "image_gen"):
            assert ts in disabled
        # The recovered non-configurable toolset that used to leak is suppressed.
        assert "kanban" in disabled

    def test_resolver_yields_exactly_file_and_terminal(self):
        from hermes_cli.tools_config import _get_platform_tools
        cfg = {}
        _blank_slate_minimal_toolsets(cfg)
        _blank_slate_minimize_config(cfg)
        resolved = set(_get_platform_tools(cfg, "cli"))
        assert resolved == {"file", "terminal"}

    def test_tool_schema_builder_yields_only_file_and_terminal_tools(self):
        # End-to-end: the exact schema set the agent would send to the model.
        import model_tools
        from hermes_cli.tools_config import _get_platform_tools
        cfg = {}
        _blank_slate_minimal_toolsets(cfg)
        _blank_slate_minimize_config(cfg)
        enabled = sorted(_get_platform_tools(cfg, "cli"))
        defs = model_tools.get_tool_definitions(
            enabled_toolsets=enabled, disabled_toolsets=None, quiet_mode=True
        )
        names = sorted(
            {(d.get("function") or {}).get("name") or d.get("name") for d in defs}
        )
        assert names == ["patch", "process", "read_file", "search_files",
                         "terminal", "write_file"]


class TestBlankSlateMinimizeConfig:
    def test_optional_features_turned_off(self):
        cfg = {}
        _blank_slate_minimize_config(cfg)
        assert cfg["compression"]["enabled"] is False
        assert cfg["memory"]["memory_enabled"] is False
        assert cfg["memory"]["user_profile_enabled"] is False
        assert cfg["checkpoints"]["enabled"] is False
        assert cfg["smart_model_routing"]["enabled"] is False
        assert cfg["session_reset"]["mode"] == "none"

    def test_does_not_clobber_unrelated_keys(self):
        cfg = {"model": {"provider": "openrouter", "default": "x/y"}}
        _blank_slate_minimize_config(cfg)
        # Model config is untouched by the minimizer.
        assert cfg["model"]["provider"] == "openrouter"
        assert cfg["model"]["default"] == "x/y"
