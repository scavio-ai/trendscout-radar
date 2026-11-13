# Contributing to TrendScout

Thanks for helping. Bug reports, new sources and fixes are all welcome.

## Set up

```bash
git clone https://github.com/scavio-ai/trendscout-radar && cd trendscout-radar
uv venv && uv pip install -e ".[dev]"
pytest
trendscout ui          # the app, on http://127.0.0.1:8765
```

No Scavio key is needed for development: the tests use captured responses in `tests/fixtures`, and the free sources (Google Trends, Google News, Hacker News) run without one.

## Where things live

| Path | What it does |
|---|---|
| `trendscout/sources/` | One parser per source. `free.py` for sources without a key, `scavio.py` for the rest, `registry.py` lists them all |
| `trendscout/topics.py`, `rank.py` | Matching items to topics, cross-platform terms, what is new |
| `trendscout/alerts.py`, `notify.py` | The alert text and the channels that send it |
| `trendscout/render.py`, `templates/` | Markdown, JSON and the public HTML page |
| `trendscout/app.py`, `ui/` | The local app: a standard-library server and plain JavaScript, no build step |

## Adding a source

1. Capture a real response into `tests/fixtures/` (remove anything personal).
2. Write a `parse_*` function that turns it into `Item`s, and a test that runs it on the fixture. Use the field names the API actually returns.
3. Register it in `sources/registry.py` and give it a label in `rank.py`.
4. Free sources are especially welcome: anything with a public RSS feed or JSON endpoint that works from a server.

## Pull requests

- Keep each PR to one change, with a test when behavior changes.
- `pytest` and `ruff check .` must pass.
- No new runtime dependencies without a good reason. The project runs on `httpx`, `pyyaml` and `jinja2`.
- No emojis in code, docs or UI text.
