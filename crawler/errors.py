"""Exceptions shared between transport, session pool, and orchestrator."""


class CrawlerError(Exception):
    pass


class TransportError(CrawlerError):
    """Network failure or unexpected HTTP status after retries."""


class ThrottledError(TransportError):
    """Facebook asked this session to slow down (rate limit)."""


class SessionInvalidError(TransportError):
    """The account is logged out, checkpointed, or otherwise unusable."""


class PoolExhaustedError(CrawlerError):
    """Every account in the pool is dead."""


class AcquireTimeoutError(CrawlerError):
    """No session became available within the acquire timeout."""
