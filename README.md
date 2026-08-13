# pytest-fsplit

`pytest-fsplit` is a pytest plugin for splitting a test suite into deterministic
file-level shards before pytest imports and collects unselected files.

It reads a pytest-split-compatible JSON duration file, aggregates node timings by
test file, assigns files to shards with a longest-processing-time-first plan, and
uses `pytest_ignore_collect` to prune files and directories outside the selected
shard.

## Installation

```bash
pip install pytest-fsplit
```

## Usage

First record durations from a complete, unsharded run:

```bash
pytest --fsplit-store-durations
```

Then run each file shard separately:

```bash
pytest --fsplits 4 --fgroup 1
pytest --fsplits 4 --fgroup 2
pytest --fsplits 4 --fgroup 3
pytest --fsplits 4 --fgroup 4
```

The duration file defaults to `.test_durations` in the invocation directory and
can be changed with `--fsplit-durations-path`.

For non-Python collectors, provide the file patterns pytest-fsplit should treat
as shardable files:

```bash
pytest --fsplit-file-pattern "*.ipynb" --fsplits 4 --fgroup 1
```

## Behavior

- Shard indices are one-based.
- Both `--fsplits` and `--fgroup` must be supplied together.
- Files without historical timings use the median known file duration.
- Stale timing entries for deleted files are ignored.
- Missing, malformed, or unusable duration files fail immediately when sharding.
- `--fsplit-store-durations` writes the same node-duration JSON shape used by
  pytest-split.
- Older pytest-split list-of-pairs duration files are accepted when reading.
- `--fsplit-store-durations` cannot be combined with sharding because it would
  record only the selected shard.

## Compatibility

`pytest-fsplit` runs before collection, so it avoids the full-suite collection
cost paid by post-collection splitters. It honors pytest collection roots,
`python_files`, `--ignore`, `--ignore-glob`, `norecursedirs`, explicit file
arguments, marker deselection, and xdist worker startup.

If pytest-split is installed too, do not combine `--fsplits`/`--fgroup` with
pytest-split's `--splits`/`--group`; pytest-fsplit rejects that combination to
avoid applying two independent partitions.
