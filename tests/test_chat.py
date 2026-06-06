from unittest.mock import MagicMock, patch

import pytest

from app import generate_suggestions, stream_response


def _make_httpx_mock(sse_lines: list[str], status_code: int = 200):
    """Build a mock that satisfies:
       with httpx.Client(...) as client:
           with client.stream(...) as resp:
    """
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.iter_lines.return_value = iter(sse_lines)

    stream_ctx = MagicMock()
    stream_ctx.__enter__ = MagicMock(return_value=mock_resp)
    stream_ctx.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream.return_value = stream_ctx

    client_ctx = MagicMock()
    client_ctx.__enter__ = MagicMock(return_value=mock_client)
    client_ctx.__exit__ = MagicMock(return_value=False)

    return client_ctx


_MESSAGES = [{"role": "user", "content": "hello"}]


def test_yields_tokens():
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.return_value = _make_httpx_mock(["data: Hello", "data:  world"])
        result = list(stream_response(_MESSAGES, "tok"))
    assert result == ["Hello", " world"]


def test_empty_stream():
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.return_value = _make_httpx_mock([])
        result = list(stream_response(_MESSAGES, "tok"))
    assert result == []


def test_connection_error_propagates():
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.return_value = _make_httpx_mock(["data: x"], status_code=401)
        with pytest.raises(ConnectionError):
            list(stream_response(_MESSAGES, "tok"))


def test_non_sse_lines_filtered():
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.return_value = _make_httpx_mock(["data: Hello", ": keep-alive", "data:  world"])
        result = list(stream_response(_MESSAGES, "tok"))
    assert result == ["Hello", " world"]


# ── generate_suggestions ─────────────────────────────────────────────────────

def test_suggestions_returns_list():
    reply = "What is X?\nHow does Y work?\nWhy is Z important?"
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.return_value = _make_httpx_mock([f"data: {reply}"])
        result = generate_suggestions(_MESSAGES, "tok")
    assert result == ["What is X?", "How does Y work?", "Why is Z important?"]


def test_suggestions_empty_on_error():
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.side_effect = Exception("API down")
        result = generate_suggestions(_MESSAGES, "tok")
    assert result == []


def test_suggestions_strips_blank_lines():
    reply = "Question one?\n\n\nQuestion two?\n"
    with patch("app.httpx.Client") as mock_cls:
        mock_cls.return_value = _make_httpx_mock([f"data: {reply}"])
        result = generate_suggestions(_MESSAGES, "tok")
    assert result == ["Question one?", "Question two?"]
