# Local web search for tool calls

Run SearXNG with JSON output enabled. Create `settings.yml`:

```yaml
use_default_settings: true
search:
  formats:
    - html
    - json
server:
  secret_key: "replace-with-a-random-local-secret"
  limiter: false
```

Create `compose.yml` beside it:

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml:ro
```

Run `docker compose up -d`. Ames uses `http://localhost:8080` by default; set
`AMES_SEARXNG_URL` to change it. If local search fails and `BRAVE_API_KEY` is
set, Ames sends the query to Brave Search API. Without either backend, the tool
returns a safe error receipt. Web search sends queries outside the machine; do
not use it for private context. Tools are invoked only on validated model calls.
