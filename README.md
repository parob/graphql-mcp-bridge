# Bridge — hosted MCP proxy for any GraphQL API

Bridge turns any public GraphQL endpoint into a set of MCP tools without you
writing any code or hosting anything. Paste the upstream URL into your MCP
client, send your own auth headers, and Bridge forwards every request
through to the upstream as the authenticated user — including the
introspection request that discovers the schema, so upstreams that require
auth for introspection (GitHub, API gateways) work too.

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
BRIDGE_ALLOW_INTERNAL_HOSTS=true uv run uvicorn bridge.app:app --reload
```

Then point an MCP client at
`http://localhost:8000/mcp/https%3A%2F%2Fcountries.trevorblades.com%2Fgraphql`.

## Configuration

All settings are read from env vars at process start:

| Variable                             | Default                                           | Meaning                                                    |
| ------------------------------------ | ------------------------------------------------- | ---------------------------------------------------------- |
| `BRIDGE_CACHE_MAXSIZE`               | `1000`                                            | Max cached instances (one per upstream + credential set)   |
| `BRIDGE_CACHE_TTL_SECONDS`           | `3600`                                            | Time-to-live for a cached introspection result             |
| `BRIDGE_UPSTREAM_TIMEOUT_SECONDS`    | `30`                                              | Request timeout to the upstream                            |
| `BRIDGE_MAX_UPSTREAM_URL_LENGTH`     | `2048`                                            | Reject upstream URLs longer than this                      |
| `BRIDGE_RATE_LIMIT`                  | `60/minute`                                       | Per-IP token bucket (e.g. `10/second`, `1000/hour`)        |
| `BRIDGE_FORWARD_HEADERS`             | common credential headers (see `bridge/config.py`) | Comma-separated header allowlist, or `*` for all safe headers. Forwarded headers also key the cache, so `*` gives every distinct client header set its own entry |
| `BRIDGE_ADMIN_SECRET`                | *(unset)*                                         | If set, enables `POST /admin/invalidate` (drops every cached instance of the given `url`) |
| `BRIDGE_USER_AGENT`                  | `graphql-mcp-bridge/0.1 (+...)`                   | User-Agent sent to the upstream                            |

## How caching works

Each upstream is introspected once and the resulting tool set is cached for
`BRIDGE_CACHE_TTL_SECONDS`. The introspection request carries the caller's
forwarded headers, and the cache entry is keyed by the upstream URL plus a
digest of those headers, so callers with different credentials never share an
instance (they may legitimately see different schemas). The headers are used
for that one request and are not stored; every tool call forwards its own.

Bridge logs one JSON line per proxied request (`bridge_request`: upstream
host, kind, status, latency, cache hit/miss) to stderr, which Cloud Logging
picks up as a structured entry.

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
