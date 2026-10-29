"""Exceptions with an exit-code contract used by the CLI."""

from __future__ import annotations


class DockyardError(Exception):
    """A problem the user can act on: bad input, missing tool, failed step. Exit code 1."""


class VerificationError(DockyardError):
    """A bundle is corrupt, altered or malformed. Exit code 2."""


class HealthError(DockyardError):
    """The application did not become healthy. Exit code 3."""
