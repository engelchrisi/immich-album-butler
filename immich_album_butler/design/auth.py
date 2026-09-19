"""Who may use design mode.

Design mode proxies thumbnails out of the photo library, so whoever reaches
this port can look at the pictures. That makes the login the actual boundary
around the library, not a convenience, and it is built accordingly:

* **No password is ever stored.** `config.toml` holds an scrypt hash, and the
  `passwd` command is the only thing that produces one -- so a config file
  that leaks is not a password that leaks.
* **Comparisons are constant time**, both for the hash and for session tokens,
  so neither can be guessed a character at a time.
* **Failed attempts are throttled** per user and per address. A LAN tool is
  exactly the kind of thing nobody notices being brute-forced.
* **Sessions live in memory only.** Restarting design mode logs everyone out,
  which is the behaviour you want from something started on demand.

Everything here is standard library: `hashlib.scrypt` and `secrets`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# scrypt parameters. n=2**15 costs ~50 ms and ~32 MB per attempt here, which is
# slow enough to make guessing expensive and fast enough that a login does not
# feel broken. CT113 has 2 GB, so the memory cost is affordable.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

SCHEME = "scrypt"

SESSION_HOURS = 12
SESSION_COOKIE = "butler_session"

# Lockout: five wrong guesses buys a five minute pause.
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


class AuthError(ValueError):
    """A credential or hash that cannot be used, with a message worth showing."""


# --------------------------------------------------------------------------
# passwords
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash a password for `config.toml`. The only way a hash should be made."""
    if not password:
        raise AuthError("the password is empty")
    if len(password) < 8:
        raise AuthError("use at least 8 characters -- this guards the photo library")
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt)
    return "$".join([SCHEME, str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
                     _b64(salt), _b64(digest)])


def verify_password(stored: str, given: str) -> bool:
    """Check a password against a stored hash, in constant time."""
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
    except (ValueError, AttributeError):
        log.warning("ignoring a password hash that is not in the expected format")
        return False
    if scheme != SCHEME:
        log.warning("ignoring a password hash with unknown scheme %r", scheme)
        return False
    try:
        salt, expected = _unb64(salt_b64), _unb64(digest_b64)
        actual = _scrypt(given, salt, int(n), int(r), int(p), len(expected))
    except (ValueError, TypeError, MemoryError) as exc:
        log.warning("could not check a password hash: %s", exc)
        return False
    return hmac.compare_digest(actual, expected)


def _scrypt(password: str, salt: bytes, n: int = SCRYPT_N, r: int = SCRYPT_R,
            p: int = SCRYPT_P, length: int = KEY_BYTES) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          dklen=length, maxmem=2 ** 26)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class User:
    """One login, as configured. The password itself is not here and never is."""

    name: str
    password_hash: str


class Users:
    """The configured logins, and the one question worth asking them."""

    def __init__(self, users: list[User]) -> None:
        self._by_name = {u.name.casefold(): u for u in users}

    def __len__(self) -> int:
        return len(self._by_name)

    @property
    def names(self) -> list[str]:
        return sorted(u.name for u in self._by_name.values())

    def check(self, name: str, password: str) -> User | None:
        """The user, if the password is right. Otherwise None.

        An unknown user still costs a hash computation, so that a wrong name
        and a wrong password take the same time and cannot be told apart.
        """
        user = self._by_name.get((name or "").casefold())
        if user is None:
            _scrypt(password or "", b"decoy-salt-00000")
            return None
        return user if verify_password(user.password_hash, password or "") else None


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------

@dataclass
class Sessions:
    """Session tokens, in memory, with an expiry.

    In memory on purpose: design mode is started when needed and stops itself
    when idle, and a session that outlived the process would be a token sitting
    on disk for no reason.
    """

    hours: int = SESSION_HOURS
    _tokens: dict[str, tuple[str, float]] = field(default_factory=dict)

    def create(self, user: User) -> str:
        self._sweep()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = (user.name, time.time() + self.hours * 3600)
        return token

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        self._sweep()
        # Compared against every live token in constant time, so a token cannot
        # be discovered by timing a series of near misses.
        for candidate, (name, _) in self._tokens.items():
            if hmac.compare_digest(candidate, token):
                return name
        return None

    def drop(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)

    def _sweep(self) -> None:
        now = time.time()
        for token in [t for t, (_, expires) in self._tokens.items() if expires <= now]:
            del self._tokens[token]


@dataclass
class Throttle:
    """Slows down guessing, counted per user and per address."""

    max_attempts: int = MAX_ATTEMPTS
    lockout: float = LOCKOUT_SECONDS
    _failures: dict[str, tuple[int, float]] = field(default_factory=dict)

    def locked_for(self, key: str) -> float:
        """Seconds still to wait, or 0."""
        count, until = self._failures.get(key, (0, 0.0))
        if count >= self.max_attempts and until > time.time():
            return until - time.time()
        return 0.0

    def failed(self, key: str) -> None:
        count, _ = self._failures.get(key, (0, 0.0))
        count += 1
        self._failures[key] = (count, time.time() + self.lockout)
        if count >= self.max_attempts:
            log.warning("locking out %r after %d failed logins", key, count)

    def passed(self, key: str) -> None:
        self._failures.pop(key, None)
