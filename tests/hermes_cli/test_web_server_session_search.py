import asyncio

from hermes_cli import web_server


class _FakeSessionDB:
    """Fake backing the /api/sessions/search endpoint.

    The endpoint surfaces direct session-id matches first, then FTS message
    matches, deduping both by compression lineage root. This fake has no
    compression chains (get_session returns no parent), so each session is its
    own lineage root.
    """

    closed = False
    opened_read_only = None
    requested_fields = None

    def __init__(self, *args, **kwargs):
        type(self).opened_read_only = kwargs.get("read_only")

    @staticmethod
    def _source_allowed(row, source=None, sources=None, exclude_sources=None):
        row_source = row.get("source")
        if source and row_source != source:
            return False
        if sources and row_source not in sources:
            return False
        if exclude_sources and row_source in exclude_sources:
            return False
        return True

    def search_sessions_by_id(
        self,
        query,
        limit=20,
        include_archived=True,
        source=None,
        sources=None,
        exclude_sources=None,
    ):
        # Kept for the method's own unit coverage; the /api/sessions/search
        # consumer no longer uses it (issue #14 routes arbitrary-infix id
        # through the whole-store metadata seam instead).
        return []

    def list_sessions_rich(self, **kwargs):
        assert kwargs.get("search_query") == "20260603"
        assert kwargs.get("order_by_last_active") is True
        assert kwargs.get("include_archived") is True
        return [
            {
                "id": "20260603_090200_exact",
                "preview": "ID match preview",
                "source": "cli",
                "model": "claude",
                "started_at": 100,
            }
        ]

    def search_messages(
        self,
        query,
        source_filter=None,
        exclude_sources=None,
        limit=20,
        fields=None,
    ):
        assert query == "20260603*"
        type(self).requested_fields = fields
        rows = [
            {
                "session_id": "20260603_090200_exact",
                "snippet": "duplicate content hit should not replace ID hit",
                "role": "user",
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "session_id": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
        return [
            row
            for row in rows
            if self._source_allowed(
                row, sources=source_filter, exclude_sources=exclude_sources
            )
        ][:limit]

    def get_session(self, session_id):
        # No compression chains in this fixture — every session is its own root.
        # The raw query "20260603" is NOT an exact id (the full id is
        # "20260603_090200_exact"), so the exact-ID B-tree path yields nothing
        # and the match comes through the metadata discovery lane.
        if session_id == "20260603":
            return None
        return {"id": session_id, "parent_session_id": None}

    def get_session_rich_row(self, session_id):
        return None

    def get_compression_tip(self, session_id):
        return session_id

    def close(self):
        self.closed = True


def test_desktop_session_search_merges_id_matches_before_content_matches(monkeypatch):
    _FakeSessionDB.opened_read_only = None
    _FakeSessionDB.requested_fields = None
    monkeypatch.setattr("hermes_state.SessionDB", _FakeSessionDB)

    response = asyncio.run(web_server.search_sessions(q="20260603", limit=2))

    assert _FakeSessionDB.requested_fields is not None
    assert "context" not in _FakeSessionDB.requested_fields
    # ID match surfaces first; the content hit on the SAME session is deduped
    # by lineage root (not double-listed); the unrelated content hit follows.
    assert response == {
        "results": [
            {
                "id": "20260603_090200_exact",
                "session_id": "20260603_090200_exact",
                "lineage_root": "20260603_090200_exact",
                "snippet": "ID match preview",
                "role": None,
                "source": "cli",
                "model": "claude",
                "session_started": 100,
            },
            {
                "id": "content_session",
                "session_id": "content_session",
                "lineage_root": "content_session",
                "snippet": "content hit",
                "role": "assistant",
                "source": "desktop",
                "model": "gpt",
                "session_started": 200,
            },
        ]
    }
    assert _FakeSessionDB.opened_read_only is True


class _ExactIdFakeSessionDB(_FakeSessionDB):
    """Variant where the raw query IS an exact session id, exercising the
    exact-ID B-tree first-win priority of /api/sessions/search."""

    def get_session(self, session_id):
        if session_id == "20260603":
            return {
                "id": "20260603",
                "parent_session_id": None,
                "source": "cli",
                "model": "claude",
                "started_at": 100,
                "preview": "exact id preview",
            }
        return {"id": session_id, "parent_session_id": None}

    def list_sessions_rich(self, **kwargs):
        # The exact B-tree hit already surfaced; no metadata rows to add.
        return []


def test_desktop_session_search_exact_id_hit_surfaces_first(monkeypatch):
    _ExactIdFakeSessionDB.opened_read_only = None
    _ExactIdFakeSessionDB.requested_fields = None
    monkeypatch.setattr("hermes_state.SessionDB", _ExactIdFakeSessionDB)

    response = asyncio.run(web_server.search_sessions(q="20260603", limit=2))

    # The exact-ID B-tree hit is first and wins lineage dedupe over the
    # message-content hit on the same logical conversation.
    first = response["results"][0]
    assert first["id"] == "20260603"
    assert first["session_id"] == "20260603"
    assert first["lineage_root"] == "20260603"
    assert first["snippet"] == "exact id preview"
