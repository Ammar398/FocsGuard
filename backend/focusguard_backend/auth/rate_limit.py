"""Login attempt throttling (spec section 51).

WHY THIS EXISTS
---------------
Argon2id makes one password guess expensive for the attacker - about 50 ms on the
reference machine. It does not make ten thousand guesses impossible, it only makes
them slow. Worse, it makes them expensive *for us*: 64 MB of memory per attempt
means a guessing run is also a denial-of-service against a 16 GB machine.

So attempts are counted and refused before the hash is ever computed.

TWO KEYS, NOT ONE
-----------------
Counting per account alone lets one attacker lock every user out of their own
account by failing their logins deliberately. Counting per IP alone lets a
distributed attacker walk straight past it. Both are counted, and either can trip.

The account limit is deliberately the looser of the two, for that lockout reason:
the address doing the guessing is throttled hard, while the victim's own account
stays usable from their real machine for longer.

IN-MEMORY, AND HONEST ABOUT IT
------------------------------
A dict in one process. That is correct for this project - a single uvicorn worker
on one machine - and wrong for a horizontally scaled deployment, where the counter
must move to Redis. The interface below is deliberately narrow so that swap is one
class, not a rewrite. Being explicit about the limitation beats pretending the
problem is solved.

SUCCESS CLEARS THE COUNT
------------------------
A legitimate user who mistypes twice and then succeeds should not be closer to a
lockout for the rest of the window.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from focusguard_shared.utilities import get_logger

logger = get_logger("backend.auth.rate_limit")

#: Per source address. Tight: a single machine making this many failed logins in
#: five minutes is not a person who forgot their password.
IP_MAX_ATTEMPTS = 10

#: Per account, and looser on purpose - see the lockout note above.
ACCOUNT_MAX_ATTEMPTS = 20

WINDOW_SECONDS = 300.0


@dataclass
class _Bucket:
    """Failure timestamps for one key, oldest first."""

    attempts: list[float] = field(default_factory=list)

    def prune(self, now: float, window: float) -> None:
        cutoff = now - window
        # The list is ordered, so dropping from the front is enough.
        while self.attempts and self.attempts[0] < cutoff:
            self.attempts.pop(0)


class RateLimiter:
    """A sliding-window counter of failed attempts."""

    def __init__(
        self,
        max_attempts: int = IP_MAX_ATTEMPTS,
        window_seconds: float = WINDOW_SECONDS,
    ) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._buckets: dict[str, _Bucket] = {}
        # FastAPI serves sync routes on a thread pool, so two logins really can
        # touch the same bucket at once.
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float | None:
        """Seconds until `key` may try again, or None if it may try now.

        Called *before* hashing, so a blocked attempt costs nothing.
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return None

            bucket.prune(moment, self._window)
            if len(bucket.attempts) < self._max:
                return None

            # Blocked until the oldest recorded failure leaves the window.
            retry_after = bucket.attempts[0] + self._window - moment
            return max(retry_after, 1.0)

    def record_failure(self, key: str, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            bucket.prune(moment, self._window)
            bucket.attempts.append(moment)

            if len(bucket.attempts) == self._max:
                # Logged without the key's value: it is an email or an address,
                # and the log is not the place for either.
                logger.warning("A login rate limit was reached (%d attempts)", self._max)

    def clear(self, key: str) -> None:
        """Forget a key's failures. Called on a successful login."""
        with self._lock:
            self._buckets.pop(key, None)

    def reset(self) -> None:
        """Drop every bucket. Used by tests."""
        with self._lock:
            self._buckets.clear()


#: Process-wide limiters. Separate instances so the two limits are independent.
ip_limiter = RateLimiter(max_attempts=IP_MAX_ATTEMPTS)
account_limiter = RateLimiter(max_attempts=ACCOUNT_MAX_ATTEMPTS)


def reset_all() -> None:
    """Clear both limiters. Used by tests, which must not inherit each other's state."""
    ip_limiter.reset()
    account_limiter.reset()
