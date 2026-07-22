import asyncio
import json
from types import SimpleNamespace

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter


def _lifecycle_payloads(caplog):
    payloads = []
    for record in caplog.records:
        if record.name != "hermes.lifecycle":
            continue
        prefix = "HERMES_LIFECYCLE "
        if record.message.startswith(prefix):
            payloads.append(json.loads(record.message[len(prefix):]))
    return payloads


def test_discord_send_delivery_has_parent_correlation_and_aggregates(caplog):
    async def scenario():
        adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
        channel = SimpleNamespace(
            send=lambda **kwargs: None,
            fetch_message=None,
        )

        async def send(**kwargs):
            return SimpleNamespace(id=1234)

        channel.send = send
        adapter._client = SimpleNamespace(
            get_channel=lambda _chat_id: channel,
            fetch_channel=None,
        )
        result = await adapter.send(
            "555",
            "hello",
            metadata={
                "trace_id": "turn-1",
                "turn_id": "turn-1",
                "api_request_id": "turn-1:api:1",
                "parent_span_id": "stream-1",
                "session_id": "session-1",
                "platform": "discord",
            },
        )
        assert result.success is True

    with caplog.at_level("INFO", logger="hermes.lifecycle"):
        asyncio.run(scenario())

    events = _lifecycle_payloads(caplog)
    assert [event["event"] for event in events] == [
        "DELIVERY_START",
        "DELIVERY_END",
    ]
    assert events[0]["trace_id"] == "turn-1"
    assert events[0]["parent_span_id"] == "stream-1"
    assert events[1]["status"] == "completed"
    assert events[1]["chunk_count"] == 1
    assert events[1]["char_count"] == 5
    assert events[1]["byte_count"] == 5
    assert all("hello" not in json.dumps(event) for event in events)


def test_discord_edit_failure_has_error_type_without_response_payload(caplog):
    async def scenario():
        async def edit(**_kwargs):
            raise RuntimeError("secret answer must not enter lifecycle telemetry")

        message = SimpleNamespace(edit=edit)
        channel = SimpleNamespace(fetch_message=lambda _message_id: None)

        async def fetch_message(_message_id):
            return message

        channel.fetch_message = fetch_message
        adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
        adapter._client = SimpleNamespace(
            get_channel=lambda _chat_id: channel,
            fetch_channel=None,
        )
        result = await adapter.edit_message(
            "555",
            "1234",
            "visible answer",
            metadata={
                "trace_id": "turn-2",
                "api_request_id": "turn-2:api:1",
                "parent_span_id": "stream-2",
            },
        )
        assert result.success is False

    with caplog.at_level("INFO", logger="hermes.lifecycle"):
        asyncio.run(scenario())

    events = _lifecycle_payloads(caplog)
    assert [event["event"] for event in events] == [
        "DELIVERY_START",
        "DELIVERY_END",
    ]
    assert events[1]["status"] == "error"
    assert events[1]["error_type"] == "RuntimeError"
    assert events[1]["parent_span_id"] == "stream-2"
    assert all("secret answer" not in json.dumps(event) for event in events)
