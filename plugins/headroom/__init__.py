"""Bounded Headroom tool-result compression with optional retrieval.

The plugin is bundled but inert unless explicitly enabled. Compression runs at
Hermes' ``transform_tool_result`` hook, so failures never need to alter core
tool dispatch. Structured manual compression is dependency-free; when
``headroom-ai`` is installed, the public ``ContentRouter.compress`` API may be
used and locally stored redacted originals become retrievable through the
bounded ``headroom_retrieve`` tool.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "hermes.headroom.v2"

_ALLOWED_TOOLS = frozenset({
    "search_files",
    "browser_snapshot",
    "terminal",
    "read_file",
    "session_search",
    "lcm_grep",
    "lcm_load_session",
    "lcm_expand",
    "web_search",
    "browser_console",
})
_EXCLUDED_TOOLS = frozenset({
    "delegate_task",
    "patch",
    "write_file",
    "memory",
    "send_message",
    "clarify",
    "cronjob",
})
_DEFAULT_ALLOWLIST = tuple(sorted(_ALLOWED_TOOLS))
_UNTRUSTED_CLOSE = "</untrusted_tool_result>"

_DEFAULT_MIN_CONTENT_CHARS = 4_000
_DEFAULT_RETRIEVE_CHARS = 8_000
_HARD_MAX_RETRIEVE_CHARS = 20_000
_MAX_RETRIEVE_OFFSET = 100_000_000

_store_instance: Any = None
_store_initialized = False
_store_lock = threading.Lock()
_router_instance: Any = None
_router_initialized = False
_router_lock = threading.Lock()


@dataclass(frozen=True)
class _Settings:
    enabled: bool
    kill_switch: bool
    allowlist: frozenset[str]
    excluded_tools: frozenset[str]
    min_content_chars: int
    max_items: int
    max_field_chars: int
    max_snapshot_chars: int
    content_router_enabled: bool
    retrieve_default_chars: int


@dataclass(frozen=True)
class _ParsedJsonResult:
    value: Any
    suffix: str


@dataclass(frozen=True)
class _WrappedResult:
    prefix: str
    body: str
    suffix: str


def _env_bool(name: str) -> Optional[bool]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def _coerce_int(value: Any, default: int, *, floor: int, ceiling: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(floor, min(ceiling, parsed))


def _load_headroom_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        block = cfg.get("headroom", {})
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def _settings() -> _Settings:
    cfg = _load_headroom_config()

    env_enabled = _env_bool("HERMES_HEADROOM_ENABLED")
    enabled = env_enabled if env_enabled is not None else bool(cfg.get("enabled", False))

    kill_switch = bool(cfg.get("kill_switch", False))
    for name in ("HERMES_HEADROOM_DISABLE", "HERMES_HEADROOM_KILL_SWITCH"):
        if _env_bool(name) is True:
            kill_switch = True

    raw_allowlist: Any = cfg.get("allowlist", _DEFAULT_ALLOWLIST)
    env_allowlist = os.environ.get("HERMES_HEADROOM_ALLOWLIST")
    if env_allowlist:
        raw_allowlist = [part.strip() for part in env_allowlist.split(",") if part.strip()]
    if not isinstance(raw_allowlist, (list, tuple, set)):
        raw_allowlist = _DEFAULT_ALLOWLIST

    raw_excluded: Any = cfg.get("excluded_tools", _EXCLUDED_TOOLS)
    if not isinstance(raw_excluded, (list, tuple, set)):
        raw_excluded = _EXCLUDED_TOOLS
    excluded_tools = frozenset(str(name) for name in raw_excluded) | _EXCLUDED_TOOLS
    allowlist = (frozenset(str(name) for name in raw_allowlist) & _ALLOWED_TOOLS) - excluded_tools

    router_cfg = cfg.get("content_router", {})
    content_router_enabled = bool(router_cfg.get("enabled", False)) if isinstance(router_cfg, dict) else False

    min_content_raw = os.environ.get(
        "HERMES_HEADROOM_MIN_CONTENT_LENGTH",
        cfg.get("min_content_chars", _DEFAULT_MIN_CONTENT_CHARS),
    )
    retrieve_chars_raw = os.environ.get(
        "HERMES_HEADROOM_RETRIEVE_MAX_CHARS",
        cfg.get("retrieve_default_chars", _DEFAULT_RETRIEVE_CHARS),
    )

    return _Settings(
        enabled=bool(enabled),
        kill_switch=bool(kill_switch),
        allowlist=allowlist,
        excluded_tools=excluded_tools,
        min_content_chars=_coerce_int(
            min_content_raw,
            _DEFAULT_MIN_CONTENT_CHARS,
            floor=256,
            ceiling=1_000_000,
        ),
        max_items=_coerce_int(cfg.get("max_items"), 8, floor=1, ceiling=50),
        max_field_chars=_coerce_int(cfg.get("max_field_chars"), 240, floor=40, ceiling=2_000),
        max_snapshot_chars=_coerce_int(
            cfg.get("max_snapshot_chars"), 2_400, floor=200, ceiling=20_000
        ),
        content_router_enabled=content_router_enabled,
        retrieve_default_chars=_coerce_int(
            retrieve_chars_raw,
            _DEFAULT_RETRIEVE_CHARS,
            floor=256,
            ceiling=_HARD_MAX_RETRIEVE_CHARS,
        ),
    )


def _get_store() -> Any:
    """Return Headroom's local compression store when the optional package exists."""
    global _store_instance, _store_initialized
    if _store_initialized:
        return _store_instance
    with _store_lock:
        if _store_initialized:
            return _store_instance
        _store_initialized = True
        try:
            from headroom.cache.compression_store import get_compression_store

            _store_instance = get_compression_store()
        except Exception:
            _store_instance = None
            logger.warning(
                "headroom: retrieval store unavailable; compression remains enabled",
                exc_info=True,
            )
    return _store_instance


def _redact_text(text: str) -> tuple[str, bool]:
    try:
        from agent.redact import redact_sensitive_text

        redacted = redact_sensitive_text(text, force=True)
    except Exception:
        redacted = text
    return redacted, redacted != text


def _redact_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            redacted, item_changed = _redact_value(item)
            changed = changed or item_changed
            out.append(redacted)
        return out, changed
    if isinstance(value, dict):
        changed = False
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out_key = key
            if isinstance(key, str):
                out_key, key_changed = _redact_text(key)
                changed = changed or key_changed
            redacted, item_changed = _redact_value(item)
            changed = changed or item_changed
            out[out_key] = redacted
        return out, changed
    return value, False


def _redacted_original(result: str) -> str:
    """Persist only a redacted representation of the original tool result."""
    parsed = _parse_json_result(result)
    if parsed is None:
        return _redact_text(result)[0]
    redacted, _ = _redact_value(parsed.value)
    rendered = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
    if parsed.suffix:
        rendered += parsed.suffix
    return rendered


def _store_for_retrieval(result: str, tool_name: str, **kwargs: Any) -> Optional[str]:
    store = _get_store()
    if store is None:
        return None
    try:
        redacted_original = _redacted_original(result)
        handle = store.store(original=redacted_original, compressed="", tool_name=tool_name)
        return str(handle) if handle else None
    except Exception:
        logger.warning(
            "headroom: failed to persist retrievable content for tool %s; compression will continue without retrieval",
            tool_name,
            exc_info=True,
        )
        return None


def _bounded_retrieve(args: dict[str, Any] | None, **kwargs: Any) -> str:
    """Retrieve a bounded character page from a stored redacted original."""
    args = args or {}
    hash_key = args.get("hash")
    if not isinstance(hash_key, str) or not hash_key.strip():
        return json.dumps({"success": False, "error": "missing or invalid hash parameter"})
    hash_key = hash_key.strip()
    if len(hash_key) > 128 or any(ch not in "0123456789abcdefABCDEF" for ch in hash_key):
        return json.dumps({"success": False, "error": "invalid retrieval hash"})

    offset = _coerce_int(args.get("offset", 0), 0, floor=0, ceiling=_MAX_RETRIEVE_OFFSET)
    requested = _coerce_int(
        args.get("max_chars", _settings().retrieve_default_chars),
        _settings().retrieve_default_chars,
        floor=1,
        ceiling=_HARD_MAX_RETRIEVE_CHARS,
    )

    store = _get_store()
    if store is None:
        return json.dumps({"success": False, "error": "retrieval store unavailable"})
    try:
        entry = store.retrieve(hash_key)
    except Exception:
        logger.warning("headroom: retrieval failed for hash %s", hash_key, exc_info=True)
        return json.dumps({"success": False, "error": "retrieval failed"})

    content = getattr(entry, "original_content", None) if entry is not None else None
    if not isinstance(content, str):
        return json.dumps({"success": False, "error": "content not found (may have expired)"})

    total_chars = len(content)
    start = min(offset, total_chars)
    page = content[start : start + requested]
    next_offset = start + len(page)
    truncated = next_offset < total_chars
    return json.dumps(
        {
            "success": True,
            "hash": hash_key,
            "offset": start,
            "returned_chars": len(page),
            "total_chars": total_chars,
            "next_offset": next_offset if truncated else None,
            "truncated": truncated,
            "content": page,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _split_untrusted_wrapper(result: str) -> Optional[_WrappedResult]:
    leading_len = len(result) - len(result.lstrip())
    leading = result[:leading_len]
    rest = result[leading_len:]
    if not rest.startswith("<untrusted_tool_result"):
        return None
    close_idx = rest.rfind(_UNTRUSTED_CLOSE)
    if close_idx < 0:
        return None
    suffix_start = close_idx
    if suffix_start > 0 and rest[suffix_start - 1] == "\n":
        suffix_start -= 1
    header_end = rest.find("\n\n")
    if header_end < 0 or header_end + 2 > suffix_start:
        return None
    body_start = header_end + 2
    return _WrappedResult(
        prefix=leading + rest[:body_start],
        body=rest[body_start:suffix_start],
        suffix=rest[suffix_start:],
    )


def _parse_json_result(result: str) -> Optional[_ParsedJsonResult]:
    try:
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(result)
    except (TypeError, ValueError):
        return None
    suffix = result[end:]
    if suffix.strip() and not suffix.lstrip().startswith("[Hint:"):
        return None
    return _ParsedJsonResult(value=value, suffix=suffix)


def _clip_text(value: Any, max_chars: int) -> str:
    text = str(value)
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    last_nl = clipped.rfind("\n")
    if last_nl > max_chars // 2:
        clipped = clipped[:last_nl]
    omitted = len(text) - len(clipped)
    return f"{clipped}\n[headroom: truncated {omitted} chars]"


def _ordered_unique(values: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _compress_search_files(data: Any, settings: _Settings) -> Optional[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("error"):
        return None
    compressed: dict[str, Any] = {
        "total_count": data.get("total_count", 0),
        "truncated": bool(data.get("truncated", False)),
    }
    matches = data.get("matches")
    if isinstance(matches, list):
        sample = []
        paths: list[str] = []
        for item in matches[: settings.max_items]:
            if isinstance(item, dict):
                path = str(item.get("path", ""))
                if path:
                    paths.append(path)
                sample.append({
                    "path": path,
                    "line": item.get("line"),
                    "content": _clip_text(item.get("content", ""), settings.max_field_chars),
                })
            else:
                sample.append({"content": _clip_text(item, settings.max_field_chars)})
        compressed.update({
            "kind": "matches",
            "returned_count": len(matches),
            "omitted_count": max(0, len(matches) - len(sample)),
            "paths": _ordered_unique(paths, settings.max_items),
            "matches": sample,
        })
        return compressed
    files = data.get("files")
    if isinstance(files, list):
        sample_files = [str(item) for item in files[: settings.max_items]]
        compressed.update({
            "kind": "files",
            "returned_count": len(files),
            "omitted_count": max(0, len(files) - len(sample_files)),
            "files": sample_files,
        })
        return compressed
    counts = data.get("counts")
    if isinstance(counts, dict):
        sorted_counts = sorted(
            counts.items(),
            key=lambda item: item[1] if isinstance(item[1], int) else 0,
            reverse=True,
        )
        compressed.update({
            "kind": "counts",
            "returned_count": len(counts),
            "omitted_count": max(0, len(counts) - settings.max_items),
            "counts": [
                {"path": str(path), "count": count}
                for path, count in sorted_counts[: settings.max_items]
            ],
        })
        return compressed
    return None


def _compact_metadata(value: Any, settings: _Settings) -> Any:
    if isinstance(value, str):
        return _clip_text(value, settings.max_field_chars)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_compact_metadata(item, settings) for item in value[: settings.max_items]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys(), key=str)[: settings.max_items]:
            out[str(key)] = _compact_metadata(value[key], settings)
        if len(value) > settings.max_items:
            out["_headroom_omitted_keys"] = len(value) - settings.max_items
        return out
    return _clip_text(value, settings.max_field_chars)


def _compress_browser_snapshot(data: Any, settings: _Settings) -> Optional[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("error") or data.get("success") is False:
        return None
    snapshot = data.get("snapshot")
    if not isinstance(snapshot, str):
        return None
    clipped_snapshot = _clip_text(snapshot, settings.max_snapshot_chars)
    metadata = {
        key: _compact_metadata(value, settings)
        for key, value in data.items()
        if key not in {"snapshot", "success"}
    }
    return {
        "success": data.get("success", True),
        "kind": "browser_snapshot",
        "snapshot": clipped_snapshot,
        "snapshot_stats": {
            "original_chars": len(snapshot),
            "returned_chars": len(clipped_snapshot),
            "original_lines": len(snapshot.splitlines()),
            "omitted_chars": max(0, len(snapshot) - len(clipped_snapshot)),
        },
        "metadata": metadata,
    }


def _compress_via_router(result: str) -> Optional[tuple[str, str]]:
    """Use only Headroom's public ContentRouter API; never mutate internals."""
    global _router_instance, _router_initialized
    if not _router_initialized:
        with _router_lock:
            if not _router_initialized:
                _router_initialized = True
                try:
                    from headroom.transforms.content_router import ContentRouter

                    _router_instance = ContentRouter()
                except Exception:
                    _router_instance = None
                    logger.warning(
                        "headroom: ContentRouter unavailable; falling back to structured compression",
                        exc_info=True,
                    )
    if _router_instance is None:
        return None
    try:
        router_result = _router_instance.compress(result)
        compressed = getattr(router_result, "compressed", None)
        if not isinstance(compressed, str) or len(compressed) >= len(result):
            return None
        strategy = str(getattr(router_result, "strategy_used", "content_router"))
        return compressed, strategy
    except Exception:
        logger.warning("headroom: ContentRouter.compress failed; preserving fallback path", exc_info=True)
        return None


def _source_scope(**kwargs: Any) -> dict[str, str]:
    scope: dict[str, str] = {}
    for key in ("session_id", "task_id", "tool_call_id", "turn_id"):
        value = kwargs.get(key)
        if value:
            scope[key] = str(value)[:128]
    return scope


def _retrieval_metadata(handle: Optional[str]) -> dict[str, Any]:
    if handle is None:
        return {"available": False, "reason": "storage_unavailable", "version": "redacted"}
    return {
        "available": True,
        "hash": handle,
        "tool": "headroom_retrieve",
        "version": "redacted",
        "reason": "stored_locally",
    }


def _compress_result(
    *,
    tool_name: str,
    result: str,
    settings: _Settings,
    hook_kwargs: dict[str, Any],
) -> Optional[str]:
    if len(result) < settings.min_content_chars:
        return None

    if settings.content_router_enabled:
        router_result = _compress_via_router(result)
        if router_result is not None:
            router_compressed, router_strategy = router_result
            redacted_router, had_redaction = _redact_text(router_compressed)
            handle = _store_for_retrieval(result=result, tool_name=tool_name, **hook_kwargs)
            final = {
                "compressed_body": redacted_router,
                "_headroom": {
                    "schema_version": SCHEMA_VERSION,
                    "compressed": True,
                    "tool": tool_name,
                    "original_chars": len(result),
                    "redacted": had_redaction,
                    "scope": _source_scope(**hook_kwargs),
                    "content_router": router_strategy,
                    "retrieval": _retrieval_metadata(handle),
                },
            }
            serialized = json.dumps(final, ensure_ascii=False, sort_keys=True)
            return serialized if len(serialized) < len(result) else None

    parsed_result = _parse_json_result(result)
    if parsed_result is None:
        return None
    redacted, had_redaction = _redact_value(parsed_result.value)
    if tool_name == "search_files":
        payload = _compress_search_files(redacted, settings)
    elif tool_name == "browser_snapshot":
        payload = _compress_browser_snapshot(redacted, settings)
    else:
        payload = None
    if payload is None:
        return None

    handle = _store_for_retrieval(result=result, tool_name=tool_name, **hook_kwargs)
    payload["_headroom"] = {
        "schema_version": SCHEMA_VERSION,
        "compressed": True,
        "tool": tool_name,
        "original_chars": len(result),
        "redacted": had_redaction,
        "scope": _source_scope(**hook_kwargs),
        "retrieval": _retrieval_metadata(handle),
    }
    if parsed_result.suffix:
        payload["_headroom"]["source_suffix"] = {
            "present": True,
            "chars": len(parsed_result.suffix),
            "kind": "search_files_hint",
        }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return serialized if len(serialized) < len(result) else None


def _on_transform_tool_result(
    tool_name: str = "",
    result: Any = None,
    **kwargs: Any,
) -> Optional[str]:
    settings = _settings()
    if not settings.enabled or settings.kill_switch:
        return None
    if tool_name in settings.excluded_tools or tool_name not in settings.allowlist:
        return None
    if not isinstance(result, str):
        return None

    try:
        wrapped = _split_untrusted_wrapper(result)
        if wrapped is not None:
            compressed = _compress_result(
                tool_name=tool_name,
                result=wrapped.body,
                settings=settings,
                hook_kwargs=kwargs,
            )
            if compressed is None:
                return None
            candidate = wrapped.prefix + compressed + wrapped.suffix
            return candidate if len(candidate) < len(result) else None
        return _compress_result(
            tool_name=tool_name,
            result=result,
            settings=settings,
            hook_kwargs=kwargs,
        )
    except Exception:
        logger.warning(
            "headroom: compression failed for tool %s; preserving original result",
            tool_name,
            exc_info=True,
        )
        return None


def _retrieval_tool_available() -> bool:
    settings = _settings()
    return bool(settings.enabled and not settings.kill_switch and _get_store() is not None)


def register(ctx: Any) -> None:
    """Register compression independently from optional retrieval."""
    try:
        ctx.register_hook("transform_tool_result", _on_transform_tool_result, priority=50)
    except TypeError:
        # Compatibility with pre-priority PluginContext implementations.
        try:
            ctx.register_hook("transform_tool_result", _on_transform_tool_result)
        except Exception:
            logger.warning("headroom: failed to register compression hook", exc_info=True)
    except Exception:
        logger.warning("headroom: failed to register compression hook", exc_info=True)

    try:
        ctx.register_tool(
            name="headroom_retrieve",
            toolset="headroom",
            check_fn=_retrieval_tool_available,
            schema={
                "description": (
                    "Retrieve one bounded character page of redacted original content saved by "
                    "Headroom compression. Use _headroom.retrieval.hash; continue with next_offset."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "hash": {
                            "type": "string",
                            "description": "Hex retrieval hash from _headroom.retrieval.hash",
                        },
                        "offset": {
                            "type": "integer",
                            "minimum": 0,
                            "default": 0,
                            "description": "Zero-based character offset into stored content",
                        },
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": _HARD_MAX_RETRIEVE_CHARS,
                            "default": _DEFAULT_RETRIEVE_CHARS,
                            "description": "Maximum characters returned in this page",
                        },
                    },
                    "required": ["hash"],
                    "additionalProperties": False,
                },
            },
            handler=_bounded_retrieve,
        )
    except Exception:
        logger.warning(
            "headroom: retrieval tool registration failed; compression remains available",
            exc_info=True,
        )
