"""Errors raised by agents when they refuse to answer instead of inventing data.

In the production path (`require_real_data` in the run config) an agent must never fall back
to its built-in sample fixtures: it raises one of these, the node latches
`halted_for_escalation`, and the reason is recorded on the run.
"""


class MissingRealDataError(RuntimeError):
    """An agent was asked to run in real-data mode but was given no facility data."""


class UnknownAssetError(RuntimeError):
    """A what-if query names an asset that is not in the facility."""


class NoAssetsError(RuntimeError):
    """The facility has no assets, so there is nothing to simulate."""
