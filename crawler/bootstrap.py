"""Wire accounts, transports, and limiters into a session pool."""
from .accounts import PooledSession, SessionPool
from .ratelimit import AimdLimiter
from .transport import FacebookTransport


def build_pool(accounts, settings):
    sessions = [
        PooledSession(
            account=account,
            limiter=AimdLimiter(settings.pacing),
            transport=FacebookTransport(account, settings.transport, settings.graphql_url),
        )
        for account in accounts
    ]
    return SessionPool(sessions, settings.pool)
