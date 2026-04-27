"""Tests for Beatport API client (search, add_track, retry logic)."""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beatport_sync import api

bp = types.SimpleNamespace(Beatport=api.Beatport)


def _make_response(status: int, json_body: dict | None = None, text: str = "") -> httpx.Response:
    """Build a minimal fake httpx.Response."""
    import json as _json
    body = _json.dumps(json_body or {}).encode()
    return httpx.Response(
        status_code=status,
        content=body,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "https://api.beatport.com/v4/test"),
    )


@pytest.fixture
def mock_client():
    return MagicMock(spec=httpx.Client)


@pytest.fixture
def beatport_api(mock_client) -> bp.Beatport:
    return bp.Beatport(client=mock_client)


class TestSearchTracks:
    def test_returns_tracks_on_success_nested(self, beatport_api, mock_client):
        tracks = [{"id": 1, "name": "Glue", "artists": [{"name": "Bicep"}]}]
        mock_client.request.return_value = _make_response(
            200, {"tracks": {"data": tracks}}
        )
        result = beatport_api.search_tracks("Bicep Glue")
        assert result == tracks

    def test_returns_tracks_on_success_flat_list(self, beatport_api, mock_client):
        tracks = [{"id": 1, "name": "Glue", "artists": [{"name": "Bicep"}]}]
        mock_client.request.return_value = _make_response(200, {"tracks": tracks})
        result = beatport_api.search_tracks("Bicep Glue")
        assert result == tracks

    def test_returns_tracks_on_success_top_level_list(self, beatport_api, mock_client):
        tracks = [{"id": 1, "name": "Glue", "artists": [{"name": "Bicep"}]}]
        import json as _json
        body = _json.dumps(tracks).encode()
        resp = httpx.Response(
            status_code=200, content=body,
            headers={"content-type": "application/json"},
            request=httpx.Request("GET", "https://api.beatport.com/v4/test"),
        )
        mock_client.request.return_value = resp
        result = beatport_api.search_tracks("Bicep Glue")
        assert result == tracks

    def test_returns_empty_list_on_genuine_no_results(self, beatport_api, mock_client):
        # Primary returns empty, fallback also returns empty
        mock_client.request.return_value = _make_response(
            200, {"tracks": [], "results": []}
        )
        result = beatport_api.search_tracks("xyznotatrack12345")
        assert result == []

    def test_returns_none_on_http_error(self, beatport_api, mock_client):
        mock_client.request.return_value = _make_response(500)
        result = beatport_api.search_tracks("query")
        assert result is None

    def test_returns_none_on_exception(self, beatport_api, mock_client):
        mock_client.request.side_effect = httpx.ConnectError("network down")
        result = beatport_api.search_tracks("query")
        assert result is None

    def test_tries_fallback_when_primary_empty(self, beatport_api, mock_client):
        fallback_tracks = [{"id": 2, "name": "Found via fallback"}]
        mock_client.request.side_effect = [
            _make_response(200, {"tracks": []}),
            _make_response(200, {"results": fallback_tracks}),
        ]
        result = beatport_api.search_tracks("something")
        assert result == fallback_tracks
        assert mock_client.request.call_count == 2

    def test_returns_none_when_fallback_also_fails(self, beatport_api, mock_client):
        mock_client.request.side_effect = [
            _make_response(200, {"tracks": []}),
            _make_response(503),
        ]
        result = beatport_api.search_tracks("something")
        assert result is None


class TestAddTrack:
    def test_success(self, beatport_api, mock_client):
        mock_client.request.return_value = _make_response(
            200, {"items": [{"id": 99}]}
        )
        result = beatport_api.add_track(1234, 5678)
        assert result["items"][0]["id"] == 99

    def test_raises_on_non_429_error(self, beatport_api, mock_client):
        mock_client.request.return_value = _make_response(403)
        with pytest.raises(httpx.HTTPStatusError):
            beatport_api.add_track(1234, 5678)


class TestRetryOn429:
    def test_retries_on_429_and_succeeds(self, beatport_api, mock_client):
        success = _make_response(200, {"items": [{"id": 1}]})
        rate_limited = _make_response(429)
        mock_client.request.side_effect = [rate_limited, success]

        with patch("time.sleep"):
            result = beatport_api.add_track(1, 2)
        assert result["items"][0]["id"] == 1
        assert mock_client.request.call_count == 2

    def test_raises_after_max_retries_on_429(self, beatport_api, mock_client):
        mock_client.request.return_value = _make_response(429)
        with patch("time.sleep"), pytest.raises(httpx.HTTPStatusError):
            beatport_api.add_track(1, 2)

    def test_calls_on_401_callback_once(self, mock_client):
        on_401 = MagicMock()
        beatport_api = bp.Beatport(client=mock_client, on_401=on_401)
        mock_client.request.side_effect = [
            _make_response(401),
            _make_response(200, {"items": []}),
        ]
        beatport_api.add_track(1, 2)
        on_401.assert_called_once()
