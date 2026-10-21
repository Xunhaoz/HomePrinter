"""Passcode login for when the service is reachable from the internet.

One shared code (ACCESS_CODE, a few letters, case-insensitive) buys a signed
session cookie. The signing key is derived from the code, so changing the code
logs everyone out. Wrong guesses are rate-limited per client and globally, so
the short code can't be brute-forced, even by a client that fakes its address.
"""

import hashlib
import hmac
import logging
import time
from collections import deque

from fastapi import HTTPException, Request, Response

from .config import Settings

log = logging.getLogger(__name__)

COOKIE = "homeprinter_session"
WINDOW_SECONDS = 15 * 60
MAX_FAILURES_PER_CLIENT = 5
MAX_FAILURES_TOTAL = 30


class Auth:
    def __init__(self, settings: Settings):
        self.code = settings.access_code.strip().lower()
        self.session_seconds = settings.session_days * 86400
        self._key = hashlib.sha256(f"homeprinter-session:{self.code}".encode()).digest()
        self._failures: dict[str, deque[float]] = {}
        self._all_failures: deque[float] = deque()
        if not self.enabled:
            log.warning("ACCESS_CODE is not set: the API is open to anyone who can reach it")

    @property
    def enabled(self) -> bool:
        return bool(self.code)

    def _sign(self, expires: int) -> str:
        return hmac.new(self._key, str(expires).encode(), hashlib.sha256).hexdigest()

    def new_token(self) -> str:
        expires = int(time.time()) + self.session_seconds
        return f"{expires}.{self._sign(expires)}"

    def valid(self, token: str | None) -> bool:
        if not self.enabled:
            return True
        expires, _, signature = (token or "").partition(".")
        if not expires.isdigit() or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature, self._sign(int(expires)))

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        while self._all_failures and self._all_failures[0] < cutoff:
            self._all_failures.popleft()
        for client in list(self._failures):
            times = self._failures[client]
            while times and times[0] < cutoff:
                times.popleft()
            if not times:
                del self._failures[client]

    def check_code(self, code: str, client: str) -> bool:
        """Compare a login attempt; raises 429 while the client (or everyone) is locked out."""
        now = time.time()
        self._prune(now)
        mine = self._failures.get(client, ())
        if len(mine) >= MAX_FAILURES_PER_CLIENT or len(self._all_failures) >= MAX_FAILURES_TOTAL:
            oldest = mine[0] if len(mine) >= MAX_FAILURES_PER_CLIENT else self._all_failures[0]
            retry = int(oldest + WINDOW_SECONDS - now) + 1
            raise HTTPException(429, "too many wrong codes, try again later", headers={"Retry-After": str(retry)})
        if hmac.compare_digest(code.strip().lower().encode(), self.code.encode()):
            self._failures.pop(client, None)
            return True
        self._failures.setdefault(client, deque()).append(now)
        self._all_failures.append(now)
        log.warning("wrong access code from %s", client)
        return False

    def set_cookie(self, request: Request, response: Response) -> None:
        response.set_cookie(
            COOKIE, self.new_token(), max_age=self.session_seconds, httponly=True, samesite="strict",
            secure=request.url.scheme == "https", path="/",
        )

    def clear_cookie(self, response: Response) -> None:
        response.delete_cookie(COOKIE, path="/")
