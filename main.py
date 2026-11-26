"""WebRouter web search and page reading tools for AstrBot."""

import asyncio
import ipaddress
import json
import uuid
from typing import Any
from urllib.parse import urlsplit

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.star.filter.command import GreedyStr
from pydantic import Field
from pydantic.dataclasses import dataclass
from web_router import AsyncWebRouter, Provider, WebRouterError

PROVIDER_NAMES = {provider.value for provider in Provider}
SNIPPET_CHARS = 500


async def is_public_url(url: str) -> bool:
    """Check that a URL is http(s) and every address its host resolves to is public.

    Args:
        url: The URL the model asked to read.

    Returns:
        True when the URL may be fetched, False for other schemes, embedded
        credentials, hosts that do not resolve, or private, loopback, link-local
        and other non-global addresses.
    """
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        if parts.username or parts.password:
            return False
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, parts.port)
        return bool(infos) and all(
            ipaddress.ip_address(info[4][0]).is_global for info in infos
        )
    except (OSError, UnicodeError, ValueError):
        return False


@dataclass
class WebRouterSearchTool(FunctionTool):
    plugin: Any = Field(default=None, repr=False)
    name: str = "webrouter_search"
    description: str = (
        "Search the web through WebRouter. Use it for current events, facts that "
        "may have changed, and anything that needs a source. Returns titles, URLs "
        "and snippets."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "max_results": {
                    "type": "integer",
                    "description": "Optional. Number of results, 1-20.",
                },
                "include_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional. Only return results from these domains.",
                },
                "exclude_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional. Leave out results from these domains.",
                },
            },
            "required": ["query"],
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "Error: query must not be empty."
        try:
            max_results = int(kwargs.get("max_results") or self.plugin.max_results)
        except (TypeError, ValueError):
            max_results = self.plugin.max_results
        try:
            response = await self.plugin.router.search(
                query,
                max_results=min(max(max_results, 1), 20),
                include_domains=kwargs.get("include_domains") or None,
                exclude_domains=kwargs.get("exclude_domains") or None,
                timeout=self.plugin.timeout,
                data_apis=False,
            )
        except WebRouterError as exc:
            return f"Error: WebRouter search failed: {exc}"

        ref_uuid = uuid.uuid4().hex[:4]
        results = [
            {
                "title": result.title or "",
                "url": result.url,
                "snippet": (result.description or result.content or "")[:SNIPPET_CHARS],
                "index": f"{ref_uuid}.{idx}",
            }
            for idx, result in enumerate(response.results, 1)
        ]
        return json.dumps(
            {"provider": response.provider, "results": results}, ensure_ascii=False
        )


@dataclass
class WebRouterExtractTool(FunctionTool):
    plugin: Any = Field(default=None, repr=False)
    name: str = "webrouter_extract"
    description: str = (
        "Read a public web page through WebRouter and return its text as markdown. "
        "Use it after webrouter_search when a snippet is not enough."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Required. The http(s) URL of the page to read.",
                },
            },
            "required": ["url"],
        }
    )

    async def call(self, context, **kwargs) -> ToolExecResult:
        url = str(kwargs.get("url", "")).strip()
        if not await is_public_url(url):
            return "Error: only public http(s) URLs can be read."
        try:
            response = await self.plugin.router.extract(
                url, format="markdown", timeout=self.plugin.timeout, data_apis=False
            )
        except WebRouterError as exc:
            return f"Error: WebRouter could not read the page: {exc}"
        if not response.documents:
            return "Error: WebRouter returned no content for this page."
        document = response.documents[0]
        return json.dumps(
            {
                "provider": response.provider,
                "url": document.url,
                "title": document.title or "",
                "content": (document.content or "")[: self.plugin.max_content_chars],
            },
            ensure_ascii=False,
        )


class WebRouterPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        config = config or {}
        self.max_results = int(config.get("max_results", 5))
        self.timeout = float(config.get("timeout", 30))
        self.max_content_chars = int(config.get("max_content_chars", 8000))

        provider_config = {}
        for name, key in (config.get("provider_keys") or {}).items():
            name = str(name).strip().lower()
            if name not in PROVIDER_NAMES:
                logger.warning(
                    f"[webrouter] Ignoring key for unknown provider {name!r}"
                )
            elif str(key).strip():
                provider_config[name] = {"api_key": str(key).strip()}
        provider = str(config.get("provider", "") or "auto").strip()
        self.router = AsyncWebRouter(provider, provider_config=provider_config)

        self.tools = [WebRouterSearchTool(plugin=self)]
        if config.get("enable_extract", True):
            self.tools.append(WebRouterExtractTool(plugin=self))
        self.context.add_llm_tools(*self.tools)

    async def terminate(self) -> None:
        for tool in self.tools:
            self.context.provider_manager.llm_tools.remove_func(tool.name)
        await self.router.aclose()

    @filter.command("webrouter", alias={"wr"})
    async def search_command(self, event: AstrMessageEvent, query: GreedyStr):
        """Search the web with WebRouter, e.g. /webrouter AstrBot plugins."""
        query = query.strip()
        if not query:
            yield event.plain_result("Usage: /webrouter <query>")
            return
        try:
            response = await self.router.search(
                query,
                max_results=self.max_results,
                timeout=self.timeout,
                data_apis=False,
            )
        except WebRouterError as exc:
            yield event.plain_result(f"WebRouter search failed: {exc}")
            return
        if not response.results:
            yield event.plain_result("No results.")
            return
        lines = [
            f"{idx}. {result.title or result.url}\n{result.url}"
            for idx, result in enumerate(response.results, 1)
        ]
        lines.append(f"(via {response.provider})")
        yield event.plain_result("\n\n".join(lines))
