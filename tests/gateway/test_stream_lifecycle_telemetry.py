import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.stream_consumer import GatewayStreamConsumer


def _lifecycle_events(caplog):
    events = []
    for record in caplog.records:
        marker = "HERMES_LIFECYCLE "
        if marker not in record.getMessage():
            continue
        events.append(json.loads(record.getMessage().split(marker, 1)[1]))
    return events


def test_stream_lifecycle_has_correlation_and_no_payload(caplog):
    asyncio.run(_test_stream_lifecycle_has_correlation_and_no_payload(caplog))


async def _test_stream_lifecycle_has_correlation_and_no_payload(caplog):
    caplog.set_level(logging.INFO, logger="hermes.lifecycle")
    adapter = MagicMock()
    adapter.MAX_MESSAGE_LENGTH = 4096
    adapter.send = AsyncMock(return_value=SimpleNamespace(success=True, message_id="msg-1"))

    consumer = GatewayStreamConsumer(
        adapter,
        "chat-1",
        telemetry_context=lambda: {
            "trace_id": "turn-1",
            "turn_id": "turn-1",
            "api_request_id": "turn-1:api:1",
            "session_id": "session-1",
            "platform": "discord",
        },
    )
    task = asyncio.create_task(consumer.run())
    consumer.on_delta("secret answer")
    consumer.on_delta(" and more")
    consumer.finish()
    await task

    events = _lifecycle_events(caplog)
    names = [event["event"] for event in events]
    assert names[0] == "STREAM_START"
    assert "STREAM_FIRST_DELTA" in names
    assert "STREAM_FLUSH" in names
    assert names[-1] == "STREAM_END"
    assert names.count("STREAM_FIRST_DELTA") == 1
    assert events[0]["trace_id"] == "turn-1"
    assert events[0]["parent_span_id"] == "turn-1:api:1"
    assert events[-1]["status"] == "completed"
    assert "secret answer" not in caplog.text
    assert "and more" not in caplog.text
