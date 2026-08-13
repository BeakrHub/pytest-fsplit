# Contributing

## Development setup

```bash
uv sync --extra dev
```

## Checks

Run the same checks as CI:

```bash
uv run ruff check .
uv run pytest -q
uv build
```

`uv build` writes artifacts to `dist/`, which is ignored by git.

## Test focus

Tests should use small synthetic pytest projects. Avoid coupling the plugin tests
to any private application test suite or repository layout.

When changing sharding behavior, prefer tests that compare complete unsharded
collection with the union of all shards. That is the strongest guard against
silently dropping files.

