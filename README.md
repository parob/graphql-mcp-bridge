# Bridge — hosted MCP proxy for any GraphQL API

Bridge turns any public GraphQL endpoint into a set of MCP tools without you
writing any code or hosting anything. Paste the upstream URL into your MCP
client, send your own auth headers, and Bridge forwards every request
through to the upstream as the authenticated user.

```
MCP endpoint: https://bridge.graphql-mcp.com/mcp/<url-encoded GraphQL URL>
Headers:      whatever your upstream expects (Authorization, X-API-Key, ...)
```

Bridge is the multi-tenant, hosted companion to the
[graphql-mcp](https://github.com/parob/graphql-mcp) library — it wraps
`GraphQLMCP.from_remote_url()` and layers caching, SSRF protection, and rate
limiting on top.

## Run locally

```bash
uv sync
# macOS quirk: uv can mark the path-source .pth file as hidden, which
# Python's site loader then skips. Unhide it once after each sync:
chflags nohidden .venv/lib/python*/site-packages/__editable__.graphql_mcp-*.pth 2>/dev/null || true

BRIDGE_ALLOW_INTERNAL_HOSTS=true uv run uvicorn bridge.app:app --reload
```

Then point an MCP client at
`http://localhost:8000/mcp/https%3A%2F%2Fcountries.trevorblades.com%2Fgraphql`.

> The local-dev `[tool.uv.sources]` entry for `graphql-mcp` should be removed
> once the library's next point release (>= 2.1.1) lands on PyPI.

## Configuration

All settings are read from env vars at process start:

| Variable                             | Default                                           | Meaning                                                    |
| ------------------------------------ | ------------------------------------------------- | ---------------------------------------------------------- |
| `BRIDGE_CACHE_MAXSIZE`               | `1000`                                            | Max cached upstreams                                       |
| `BRIDGE_CACHE_TTL_SECONDS`           | `3600`                                            | Time-to-live for a cached introspection result             |
| `BRIDGE_UPSTREAM_TIMEOUT_SECONDS`    | `30`                                              | Request timeout to the upstream                            |
| `BRIDGE_MAX_UPSTREAM_URL_LENGTH`     | `2048`                                            | Reject upstream URLs longer than this                      |
| `BRIDGE_RATE_LIMIT`                  | `60/minute`                                       | Per-IP token bucket (e.g. `10/second`, `1000/hour`)        |
| `BRIDGE_FORWARD_HEADERS`             | `authorization,x-api-key,cookie`                  | Comma-separated header allowlist, or `*` for all safe headers |
| `BRIDGE_ADMIN_SECRET`                | *(unset)*                                         | If set, enables `POST /admin/invalidate`                   |
| `BRIDGE_USER_AGENT`                  | `graphql-mcp-bridge/0.1 (+...)`                   | User-Agent sent to the upstream                            |

## Deploy

Bridge is designed for Cloud Run: single container, stateless, scale-to-zero.

```bash
gcloud run deploy graphql-mcp-bridge \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 80
```

## License

MIT
