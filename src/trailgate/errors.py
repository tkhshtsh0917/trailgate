"""Shared validation error for uninterpretable trajectory inputs."""


class SnapshotError(ValueError):
    """The snapshot cannot be interpreted without guessing."""
