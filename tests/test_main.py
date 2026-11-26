import json
from types import SimpleNamespace

import pytest
from web_router import WebRouterError

import main


class FakeRouter:
    """Record the calls the tools make and return canned WebRouter responses."""

    def __init__(self, search_response=None, extract_response=None, error=None):
        self.search_response = search_response
        self.extract_response = extract_response
        self.error = error
        self.calls = []

    async def search(self, query, **kwargs):
        self.calls.append(("search", query, kwargs))
        if self.error:
            raise self.error
        return self.search_response

    async def extract(self, url, **kwargs):
        self.calls.append(("extract", url, kwargs))
        if self.error:
            raise self.error
        return self.extract_response


def _plugin(router, **overrides):
    settings = {
        "router": router,
        "max_results": 5,
        "timeout": 30.0,
        "max_content_chars": 8000,
    }
    settings.update(overrides)
    return SimpleNamespace(**settings)


def _search_response(*results):
    return SimpleNamespace(provider="nimble", results=list(results))


def _result(title, url, description=None, content=None):
    return SimpleNamespace(
        title=title, url=url, description=description, content=content
    )


@pytest.mark.asyncio
async def test_search_maps_results_to_citation_entries():
    router = FakeRouter(
        _search_response(
            _result("AstrBot", "https://astrbot.app", description="Agent chatbot"),
            _result("Docs", "https://docs.astrbot.app", content="x" * 900),
        )
    )
    tool = main.WebRouterSearchTool(plugin=_plugin(router))

    payload = json.loads(await tool.call(None, query=" astrbot "))

    assert payload["provider"] == "nimble"
    first, second = payload["results"]
    assert first["title"] == "AstrBot"
    assert first["snippet"] == "Agent chatbot"
    assert len(second["snippet"]) == main.SNIPPET_CHARS
    assert first["index"].endswith(".1") and second["index"].endswith(".2")
    assert first["index"].split(".")[0] == second["index"].split(".")[0]


@pytest.mark.asyncio
async def test_search_sends_options_and_web_results_only():
    router = FakeRouter(_search_response())
    tool = main.WebRouterSearchTool(plugin=_plugin(router, timeout=12.0))

    await tool.call(
        None,
        query="news",
        max_results=50,
        include_domains=["example.com"],
        exclude_domains=[],
    )

    _, query, kwargs = router.calls[0]
    assert query == "news"
    assert kwargs == {
        "max_results": 20,
        "include_domains": ["example.com"],
        "exclude_domains": None,
        "timeout": 12.0,
        "data_apis": False,
    }


@pytest.mark.asyncio
async def test_search_uses_configured_count_when_model_gives_none():
    router = FakeRouter(_search_response())
    tool = main.WebRouterSearchTool(plugin=_plugin(router, max_results=7))

    await tool.call(None, query="news", max_results="many")

    assert router.calls[0][2]["max_results"] == 7


@pytest.mark.asyncio
async def test_search_rejects_empty_query_without_calling_webrouter():
    router = FakeRouter(_search_response())
    tool = main.WebRouterSearchTool(plugin=_plugin(router))

    assert (await tool.call(None, query="  ")).startswith("Error:")
    assert router.calls == []


@pytest.mark.asyncio
async def test_search_returns_webrouter_errors_to_the_model():
    router = FakeRouter(error=WebRouterError("every provider failed"))
    tool = main.WebRouterSearchTool(plugin=_plugin(router))

    result = await tool.call(None, query="news")

    assert result.startswith("Error: WebRouter search failed")
    assert "every provider failed" in result


@pytest.mark.asyncio
async def test_extract_returns_page_markdown_truncated(monkeypatch):
    async def public(url):
        return True

    monkeypatch.setattr(main, "is_public_url", public)
    document = SimpleNamespace(
        url="https://example.com/", title="Example", content="y" * 50
    )
    router = FakeRouter(
        extract_response=SimpleNamespace(provider="jina", documents=[document])
    )
    tool = main.WebRouterExtractTool(plugin=_plugin(router, max_content_chars=10))

    payload = json.loads(await tool.call(None, url="https://example.com/"))

    assert payload == {
        "provider": "jina",
        "url": "https://example.com/",
        "title": "Example",
        "content": "y" * 10,
    }
    assert router.calls[0][2] == {
        "format": "markdown",
        "timeout": 30.0,
        "data_apis": False,
    }


@pytest.mark.asyncio
async def test_extract_refuses_non_public_urls_without_calling_webrouter():
    router = FakeRouter()
    tool = main.WebRouterExtractTool(plugin=_plugin(router))

    for url in (
        "http://127.0.0.1:6185/api",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:192.168.1.1]/",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "http://[::1",
        "not a url",
    ):
        assert (await tool.call(None, url=url)).startswith("Error:"), url
    assert router.calls == []


@pytest.mark.asyncio
async def test_is_public_url_checks_every_resolved_address(monkeypatch):
    class Loop:
        async def getaddrinfo(self, host, port):
            return [
                (None, None, None, "", ("93.184.215.14", 0)),
                (None, None, None, "", ("10.1.2.3", 0)),
            ]

    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: Loop())

    assert await main.is_public_url("https://mixed.example/") is False


@pytest.mark.asyncio
async def test_is_public_url_accepts_public_address(monkeypatch):
    class Loop:
        async def getaddrinfo(self, host, port):
            return [(None, None, None, "", ("93.184.215.14", 443))]

    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: Loop())

    assert await main.is_public_url("https://example.com/page") is True


class FakeContext:
    def __init__(self):
        self.tools = []
        self.provider_manager = SimpleNamespace(
            llm_tools=SimpleNamespace(remove_func=self._remove)
        )

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)

    def _remove(self, name):
        self.tools = [tool for tool in self.tools if tool.name != name]


@pytest.mark.asyncio
async def test_plugin_registers_tools_and_passes_provider_keys(monkeypatch):
    created = {}

    class Router:
        def __init__(self, provider, provider_config):
            created.update(provider=provider, provider_config=provider_config)

        async def aclose(self):
            created["closed"] = True

    monkeypatch.setattr(main, "AsyncWebRouter", Router)
    context = FakeContext()

    plugin = main.WebRouterPlugin(
        context,
        {
            "provider": "",
            "provider_keys": {"Tavily": " tvly-test ", "exa": "", "nosuch": "k"},
            "enable_extract": True,
        },
    )

    assert created == {
        "provider": "auto",
        "provider_config": {"tavily": {"api_key": "tvly-test"}},
    }
    assert [tool.name for tool in context.tools] == [
        "webrouter_search",
        "webrouter_extract",
    ]

    await plugin.terminate()
    assert context.tools == []
    assert created["closed"] is True


def test_plugin_leaves_out_extract_when_disabled(monkeypatch):
    monkeypatch.setattr(main, "AsyncWebRouter", lambda *args, **kwargs: None)
    context = FakeContext()

    main.WebRouterPlugin(context, {"enable_extract": False})

    assert [tool.name for tool in context.tools] == ["webrouter_search"]


class FakeEvent:
    def plain_result(self, text):
        return text


@pytest.mark.asyncio
async def test_command_lists_results_and_provider(monkeypatch):
    router = FakeRouter(
        _search_response(
            _result("AstrBot", "https://astrbot.app"), _result("", "https://b.example")
        )
    )
    monkeypatch.setattr(main, "AsyncWebRouter", lambda *args, **kwargs: router)
    plugin = main.WebRouterPlugin(FakeContext(), {"max_results": 3})

    replies = [reply async for reply in plugin.search_command(FakeEvent(), "astrbot")]

    assert replies == [
        "1. AstrBot\nhttps://astrbot.app\n\n2. https://b.example\nhttps://b.example\n\n(via nimble)"
    ]
    assert router.calls[0][2]["max_results"] == 3
