import io
import json
from unittest.mock import MagicMock, patch

import pytest
from urllib import error

from hcl_graphql.client import GraphQLClient

ENDPOINT = "https://test.endpoint/graphql"


@pytest.fixture
def rate_state(tmp_path, monkeypatch):
    state_path = tmp_path / "hardcover_api_rate.ts"
    monkeypatch.setattr(
        "hcl_graphql.client._rate_state_path",
        lambda: str(state_path),
    )
    return state_path


@pytest.fixture
def client(rate_state):
    yield GraphQLClient(ENDPOINT, requests_per_minute=None)


@patch("urllib.request.urlopen")
def test_execute_no_token(urlopen, client: GraphQLClient):
    query = """
query TestQuery($test: String) {
    test(test: $test) {
        results
    }
}
"""
    vars = {"test": "foo"}
    response = json.dumps({"data": {"test": "foo"}}).encode("utf-8")
    request_body = json.dumps({"query": query, "variables": vars}).encode("utf-8")

    urlopen.return_value.__enter__.return_value.read.return_value = response

    client.execute(query, vars)
    (request,), _ = urlopen.call_args
    assert request.full_url == ENDPOINT
    assert request.data == request_body
    assert "Authorization" not in request.headers


@patch("time.sleep")
@patch("time.time")
@patch("urllib.request.urlopen")
def test_rate_limit_waits_between_requests(
    urlopen, mock_time, mock_sleep, rate_state
):
    client = GraphQLClient(ENDPOINT, requests_per_minute=30)
    response = json.dumps({"data": {"ok": True}}).encode("utf-8")
    urlopen.return_value.__enter__.return_value.read.return_value = response

    mock_time.side_effect = [100.0, 100.0, 100.5, 102.5, 102.5, 102.5]
    rate_state.write_text("100.0", encoding="utf-8")

    client.execute("query { ok }")
    client.execute("query { ok }")

    rate_limit_waits = [call.args[0] for call in mock_sleep.call_args_list]
    assert 1.5 in rate_limit_waits


@patch("time.sleep")
@patch("urllib.request.urlopen")
def test_execute_retries_on_429(urlopen, mock_sleep, rate_state):
    client = GraphQLClient(ENDPOINT, requests_per_minute=None)
    success_body = json.dumps({"data": {"ok": True}}).encode("utf-8")
    success_response = MagicMock()
    success_response.read.return_value = success_body
    success_response.__enter__.return_value = success_response

    rate_limited = error.HTTPError(
        ENDPOINT,
        429,
        "Too Many Requests",
        {"Retry-After": "2"},
        io.BytesIO(b"rate limited"),
    )
    urlopen.side_effect = [rate_limited, success_response]

    result = client.execute("query { ok }")

    assert result == {"ok": True}
    mock_sleep.assert_called_once_with(2)
    assert urlopen.call_count == 2
