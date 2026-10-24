from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from url_fetch import detect_login_wall, probe_url, shortener_warnings


def _response(
    status: int,
    url: str,
    *,
    history: list[MagicMock] | None = None,
    text: str = "<html><body>Job posting for engineer role at careers.</body></html>",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.url = url
    response.history = history or []
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.iter_content = lambda chunk_size=16384: [text.encode("utf-8")]
    response.close = MagicMock()
    return response


def test_head_403_falls_back_to_get() -> None:
    head = _response(403, "https://example.com/job")
    body = "<html><body>Open role: Backend Engineer at Acme careers department.</body></html>"
    get_resp = _response(200, "https://example.com/job", text=body)

    with patch("url_fetch._request") as mock_request:
        mock_request.side_effect = [head, get_resp]
        result = probe_url("https://example.com/job", timeout=5)

    assert mock_request.call_args_list[0][0][0] == "HEAD"
    assert mock_request.call_args_list[1][0][0] == "GET"
    assert result["valid"] is True
    assert result["fetch_method"] == "GET"
    assert result["status_code"] == 200


def test_tcs_shortener_uses_get_first() -> None:
    body = "<html><body>TCS hiring walk-in drive job careers opening.</body></html>"
    get_resp = _response(200, "https://on.tcs.com/abc", text=body)

    with patch("url_fetch._request", return_value=get_resp) as mock_request:
        result = probe_url("https://on.tcs.com/abc", timeout=5)

    assert mock_request.call_args[0][0] == "GET"
    assert result["valid"] is True
    assert any("shortener" in warning.lower() for warning in result["warnings"])


def test_login_wall_marks_invalid() -> None:
    body = "<html><body>Sign in to LinkedIn to view this job posting.</body></html>"
    get_resp = _response(200, "https://www.linkedin.com/jobs/view/1", text=body)

    with patch("url_fetch._request", return_value=get_resp):
        result = probe_url("https://www.linkedin.com/jobs/view/1", timeout=5)

    assert result["valid"] is False
    assert "login_or_access_wall" in result["flags"]
    assert any("discouraged" in flag or result["warnings"] for flag in result["flags"])


def test_shortener_warning_on_redirect_chain() -> None:
    hop = MagicMock()
    hop.url = "https://bit.ly/xyz"
    warnings = shortener_warnings(
        "https://careers.example.com/job",
        ["https://careers.example.com/job", "https://bit.ly/xyz", "https://apply.example.com/job"],
    )
    assert any("shortener" in item.lower() for item in warnings)


def test_detect_login_wall_snippet() -> None:
    assert detect_login_wall("<html>Please Sign in to LinkedIn</html>") is not None
