"""Shared pytest-fsplit exceptions."""

from __future__ import annotations


class FileShardingError(ValueError):
    """Raised when a file-shard plan cannot be constructed safely."""

