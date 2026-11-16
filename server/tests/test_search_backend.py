import aiohttp
import pytest

from server import tools


class Response:
    def __init__(self, payload=None, failure=False):
        self.payload = payload
        self.failure = failure

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        if self.failure:
            raise aiohttp.ClientError("private details must not appear in logs")

    async def json(self):
        return self.payload


class Session:
    def __init__(self, responses, requests):
        self.responses = iter(responses)
        self.requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return next(self.responses)


@pytest.fixture
def enabled_search(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "SEARCH_SETTING", tmp_path / "search-enabled.json")
    tools.set_search_enabled(True)


@pytest.mark.parametrize("fallback", [False, True])
async def test_search_uses_local_then_brave_only_if_local_fails(
    monkeypatch, enabled_search, fallback
):
    requests = []
    row = {"title": "An article", "url": "https://example.org/a", "snippet": "Brief"}
    responses = [Response(failure=True) if fallback else Response({"results": [row]})]
    if fallback:
        monkeypatch.setenv("BRAVE_API_KEY", "secret")
        responses.append(
            Response(
                {
                    "web": {
                        "results": [
                            {
                                "title": row["title"],
                                "url": row["url"],
                                "description": row["snippet"],
                            }
                        ]
                    }
                }
            )
        )
    monkeypatch.setattr(
        tools.aiohttp, "ClientSession", lambda **kwargs: Session(responses, requests)
    )
    assert await tools.search("public query") == {"results": [row]}
    assert requests[0][1]["params"] == {"q": "public query", "format": "json"}
    assert len(requests) == (2 if fallback else 1)
    if fallback:
        assert requests[1][1]["headers"] == {"X-Subscription-Token": "secret"}


async def test_search_both_fail_returns_safe_error(monkeypatch, enabled_search):
    monkeypatch.setenv("BRAVE_API_KEY", "secret")
    monkeypatch.setattr(
        tools.aiohttp, "ClientSession", lambda **kwargs: Session([Response(failure=True)] * 2, [])
    )
    with pytest.raises(RuntimeError, match="search_unavailable"):
        await tools.search("private value")


async def test_disabled_search_never_creates_network_session_and_fails_closed(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(tools, "SEARCH_SETTING", tmp_path / "search-enabled.json")
    monkeypatch.setenv("BRAVE_API_KEY", "secret")

    def forbidden_session(**kwargs):
        raise AssertionError("network was accessed")

    monkeypatch.setattr(tools.aiohttp, "ClientSession", forbidden_session)
    with pytest.raises(tools.SearchDisabled):
        await tools.execute({"name": "web_search", "arguments": {"query": "private"}})
    tools.set_search_enabled(True)
    assert tools.search_enabled()
    tools.set_search_enabled(False)
    assert not tools.search_enabled()
    with pytest.raises(tools.SearchDisabled):
        await tools.search("private")
    (tmp_path / "search-enabled.json").write_text("broken")
    assert not tools.search_enabled()
