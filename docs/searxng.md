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
`AMES_SEARXNG_URL` to change it. Web search is off by default. Turn it on in the
web UI to allow queries to leave the Mac; the setting is saved in the local Ames
data directory. The UI signals when a search runs. When search is off, no backend
is contacted and the model receives a `search_disabled` error receipt.

If local search fails while search is on and `BRAVE_API_KEY` is set, Ames sends
the query to Brave Search API. Without either backend, the tool returns a safe
error receipt. Do not use private context in queries. Tools are invoked only on
validated model calls.
