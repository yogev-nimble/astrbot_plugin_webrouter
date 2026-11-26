# astrbot_plugin_webrouter

[English](#english)

通过 [WebRouter](https://github.com/Nimbleway/webrouter) 为 AstrBot 提供联网搜索和网页读取。不填任何 API Key 即可使用。

WebRouter 是一个开源（Apache-2.0）Python 库，用同一个接口调用 Tavily、Exa、Brave、Firecrawl、Nimble 等多家搜索服务。某个服务失败或没有结果时，会自动切换到下一个。本插件由 WebRouter 的维护者编写。

## 功能

| 名称 | 类型 | 说明 |
| --- | --- | --- |
| `webrouter_search` | LLM 工具 | 联网搜索，返回标题、链接和摘要。可指定结果数量、只搜索或排除某些域名。 |
| `webrouter_extract` | LLM 工具 | 读取一个公开网页，返回 Markdown 正文。可在配置中关闭。 |
| `/webrouter 关键词`（别名 `/wr`） | 指令 | 直接在聊天中搜索，不经过模型。 |

搜索结果的格式与 AstrBot 内置搜索工具一致（`title`、`url`、`snippet`、`index`）。

## 安装

在 AstrBot WebUI 的 **插件** 页面，通过仓库地址安装：

```
https://github.com/yogev-nimble/astrbot_plugin_webrouter
```

AstrBot 会自动安装 `requirements.txt` 中的 `webrouter`。需要 AstrBot 4.16 或更高版本（Python 3.12）。

安装后，在对话中启用函数调用，模型就能使用这两个工具。如果同时开启了 AstrBot 内置的网页搜索，模型会看到两套搜索工具，建议只保留一套。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| 搜索服务 `provider` | `auto` | `auto` 时自动路由：先用已配置密钥的服务，再用免费服务。也可以填写单个服务名，例如 `tavily`。 |
| 服务密钥 `provider_keys` | 空 | 键为服务名，值为 API Key，例如 `tavily` → `tvly-...`。服务名列表见 [Providers](https://docs.webrouter.net/providers)。 |
| 默认搜索结果数量 `max_results` | 5 | 模型未指定数量时使用，范围 1-20。 |
| 请求超时 `timeout` | 30 秒 | 每次搜索或读取的总时限。 |
| 启用网页读取工具 `enable_extract` | 开启 | 关闭后不注册 `webrouter_extract`。 |
| 网页正文最大字符数 `max_content_chars` | 8000 | `webrouter_extract` 返回正文的长度上限。 |

### 关于密钥

- 不填密钥时，搜索使用提供免费额度的服务（例如 You.com、Exa 的免费 MCP 接口），网页读取使用 Jina Reader 等免费服务。免费服务有频率限制，结果可能没有发布日期。
- 每个密钥只会发送给对应的服务。
- WebRouter 也会读取 AstrBot 进程环境变量中的密钥（例如 `TAVILY_API_KEY`）。如果环境中已有某个服务的密钥，该服务也会加入自动路由，产生的费用计入该密钥。

### 关于网页读取的安全限制

`webrouter_extract` 只读取公开的 http(s) 地址：域名解析出的每个地址都必须是公网地址，带用户名密码的链接、内网、回环和链路本地地址都会被拒绝，不会发出请求。该检查在请求前做一次 DNS 解析，不能防止 DNS 重绑定。

## 开发

```bash
uvx ruff check . && uvx ruff format --check .
uv run --no-project --with astrbot --with-requirements requirements.txt --with pytest --with pytest-asyncio pytest -q
```

## 许可证

Apache-2.0

---

## English

Web search and page reading for AstrBot through [WebRouter](https://github.com/Nimbleway/webrouter), with no API key required.

WebRouter is an open-source (Apache-2.0) Python library that puts one interface in front of many search services (Tavily, Exa, Brave, Firecrawl, Nimble and more) and falls back to the next one when a call fails or comes back empty. This plugin is written by the WebRouter maintainers.

- **`webrouter_search`** (LLM tool): web search returning title, URL and snippet, in the same shape as AstrBot's built-in search tools. Optional result count and include or exclude domain lists.
- **`webrouter_extract`** (LLM tool): reads one public page and returns markdown. Can be turned off in the config.
- **`/webrouter <query>`** (alias `/wr`): search straight from the chat.

Install from the WebUI **Plugins** page with the repository URL above. Requires AstrBot 4.16 or later.

Keys are optional: enter them in the plugin config as provider name → API key. Each key is sent only to its own provider. WebRouter also reads provider keys from the AstrBot process environment (for example `TAVILY_API_KEY`); a provider whose key is there joins the automatic route and its searches are billed to that key. With no key, search uses free tiers (such as the You.com and Exa MCP endpoints), which are rate-limited and may return results with no publish date.

`webrouter_extract` refuses non-http(s) URLs, URLs with credentials, and any host that resolves to a private, loopback, link-local or other non-public address, before any request is made.
