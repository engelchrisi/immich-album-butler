"""The HTTP layer of design mode.

Deliberately small: login, routing, static files, and a thumbnail proxy.
Everything it can actually do lives in `api.py`.

What it is careful about, because this listens on the LAN and can show the
photo library:

* **A login stands in front of everything.** Users come from `config.toml` as
  scrypt hashes (see `auth.py`); there is no default account and no way in
  without one. It refuses to bind anywhere but loopback if none is configured.
* **The Immich key never reaches the browser.** Thumbnails are fetched
  server-side and re-served, so the page holds no credential at all.
* **The session cookie is HttpOnly and SameSite=Strict**, so script on another
  page cannot read it and the browser will not send it on a cross-site
  request. Writes check `Origin` as well.
"""

from __future__ import annotations

import html
import json
import logging
import socket
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..config import DesignUser
from ..immich import ImmichError
from .api import ApiError, DesignApi
from .auth import SESSION_COOKIE, Sessions, Throttle, User, Users

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css",
                 ".js": "text/javascript", ".svg": "image/svg+xml"}

LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# A "remember me" login lasts a month; a plain one lasts the working day.
REMEMBER_HOURS = 24 * 30
SESSION_HOURS = 12


class Idle:
    """Stops the server after a quiet spell, so design mode is not a daemon.

    It is started on demand and forgotten about; nothing should be listening on
    the LAN days later because somebody left a tab open in the morning.
    """

    def __init__(self, minutes: int) -> None:
        self.seconds = minutes * 60
        self.touched = time.monotonic()

    def touch(self) -> None:
        self.touched = time.monotonic()

    def watch(self, server: ThreadingHTTPServer) -> None:
        if not self.seconds:
            return
        while True:
            time.sleep(min(30, self.seconds))
            if time.monotonic() - self.touched >= self.seconds:
                log.info("no requests for %d minutes; shutting design mode down",
                         self.seconds // 60)
                threading.Thread(target=server.shutdown, daemon=True).start()
                return


def serve(api: DesignApi, host: str = "127.0.0.1", port: int = 8081,
          users: list[DesignUser] | None = None, idle_minutes: int = 30) -> None:
    """Run design mode until it is stopped or goes idle."""
    accounts = Users([User(u.name, u.password_hash) for u in (users or [])])
    if not accounts and host not in LOOPBACK:
        raise ImmichError(
            f"refusing to listen on {host} with no login configured: design mode "
            f"can show the photo library. Add a [[design.users]] entry to "
            f"config.toml -- 'immich-album-butler passwd <name>' prints one -- "
            f"or bind to 127.0.0.1.")
    if not accounts:
        log.warning("no [[design.users]] configured: design mode is open to "
                    "anyone who can reach %s. Fine for a local look, not for "
                    "a LAN.", host)

    idle = Idle(idle_minutes)
    handler = _make_handler(api, accounts, idle)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    threading.Thread(target=idle.watch, args=(server,), daemon=True).start()

    shown = host if host not in ("0.0.0.0", "::") else _best_address()
    log.info("design mode on http://%s:%d  (config %s, %d login(s))",
             shown, port, api.config_dir, len(accounts))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _best_address() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "0.0.0.0"


def _make_handler(api: DesignApi, accounts: Users, idle: Idle,
                  sessions: Sessions | None = None,
                  throttle: Throttle | None = None):
    sessions = sessions or Sessions()
    throttle = throttle or Throttle()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "immich-album-butler"
        sys_version = ""

        def log_message(self, fmt: str, *args) -> None:
            log.debug("%s %s", self.address_string(), fmt % args)

        # -- responses ----------------------------------------------------

        def _send(self, status: int, body: bytes, content_type: str,
                  headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # The UI is served from here and talks to nothing else.
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; "
                             "form-action 'self'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload, status: int = 200, headers=None) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"),
                       "application/json", headers)

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status=status)

        def _redirect(self, where: str, headers: dict | None = None) -> None:
            self._send(303, b"", "text/plain", {"Location": where, **(headers or {})})

        # -- session ------------------------------------------------------

        def _token(self) -> str | None:
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            try:
                jar = SimpleCookie(raw)
            except CookieError:
                return None
            morsel = jar.get(SESSION_COOKIE)
            return morsel.value if morsel else None

        @property
        def _user(self) -> str | None:
            if not accounts:
                return "anyone"          # unprotected loopback mode
            return sessions.user_for(self._token())

        def _require_login(self, api_call: bool) -> bool:
            if self._user is not None:
                return True
            if api_call:
                self._error(401, "not logged in")
            else:
                self._login_page()
            return False

        def _cookie(self, token: str, remember: bool) -> str:
            hours = REMEMBER_HOURS if remember else SESSION_HOURS
            # No Secure flag: this is plain HTTP on a LAN, and setting it would
            # make the browser drop the cookie entirely.
            return (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; "
                    f"SameSite=Strict; Max-Age={hours * 3600}")

        # -- login --------------------------------------------------------

        def _login_page(self, message: str = "", status: int = 200) -> None:
            template = (STATIC / "login.html").read_text(encoding="utf-8")
            page = template.replace("<!--MESSAGE-->", html.escape(message))
            self._send(status, page.encode("utf-8"), CONTENT_TYPES[".html"],
                       {"Cache-Control": "no-store"})

        def _do_login(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 8192:
                return self._login_page("That request was too large.", 400)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            name = (form.get("name") or [""])[0]
            password = (form.get("password") or [""])[0]
            remember = bool(form.get("remember"))

            # Throttled by address as well as by name, so trying many names
            # from one machine is no cheaper than trying one.
            keys = [f"user:{name.casefold()}", f"host:{self.client_address[0]}"]
            waiting = max((throttle.locked_for(k) for k in keys), default=0.0)
            if waiting:
                return self._login_page(
                    f"Too many failed attempts. Try again in "
                    f"{int(waiting // 60) + 1} minute(s).", 429)

            user = accounts.check(name, password)
            if user is None:
                for key in keys:
                    throttle.failed(key)
                log.warning("failed login for %r from %s", name,
                            self.client_address[0])
                # One message for both cases: which half was wrong is not the
                # visitor's business.
                return self._login_page("Wrong user name or password.", 401)

            for key in keys:
                throttle.passed(key)
            log.info("%s logged in from %s", user.name, self.client_address[0])
            self._redirect("/", {"Set-Cookie": self._cookie(
                sessions.create(user), remember)})

        def _do_logout(self) -> None:
            sessions.drop(self._token())
            self._redirect("/", {"Set-Cookie":
                                 f"{SESSION_COOKIE}=; Path=/; HttpOnly; "
                                 f"SameSite=Strict; Max-Age=0"})

        def _same_origin(self) -> bool:
            """A write must come from this page, not from another site."""
            origin = self.headers.get("Origin")
            if origin is None:
                return True             # curl and the like; no browser to abuse
            if urlparse(origin).netloc == self.headers.get("Host", ""):
                return True
            # Read the body we are about to refuse. Answering before the client
            # has finished sending makes the connection abort rather than carry
            # the 403, so the refusal would never arrive.
            self._discard_body()
            self._error(403, "cross-origin request refused")
            return False

        def _discard_body(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)

        # -- routing ------------------------------------------------------

        def do_GET(self) -> None:                    # noqa: N802
            idle.touch()
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

            # The login page and its stylesheet are the only public things.
            if path == "/login":
                if self._user is not None:
                    return self._redirect("/")
                return self._login_page()
            if path == "/static/style.css":
                return self._static("style.css")

            if not self._require_login(api_call=path.startswith("/api/")):
                return

            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path.startswith("/api/thumb/"):
                return self._thumbnail(path.rsplit("/", 1)[-1])
            if path == "/api/whoami":
                return self._json({"user": self._user,
                                   "protected": bool(accounts)})
            if path.startswith("/api/"):
                return self._call(lambda: self._get_api(path, query))
            self._error(404, f"no page {path}")

        do_HEAD = do_GET

        def do_POST(self) -> None:                   # noqa: N802
            idle.touch()
            path = unquote(urlparse(self.path).path)

            if path == "/login":
                if not self._same_origin():
                    return
                return self._do_login()
            if path == "/logout":
                return self._do_logout()

            if not self._require_login(api_call=True) or not self._same_origin():
                return
            try:
                body = self._read_json()
            except ValueError as exc:
                return self._error(400, str(exc))
            self._call(lambda: self._post_api(path, body))

        def do_DELETE(self) -> None:                 # noqa: N802
            idle.touch()
            if not self._require_login(api_call=True) or not self._same_origin():
                return
            path = unquote(urlparse(self.path).path)
            if path.startswith("/api/albums/"):
                return self._call(lambda: api.delete_album(path.rsplit("/", 1)[-1]))
            if path.startswith("/api/groups/"):
                return self._call(lambda: api.delete_group(path.rsplit("/", 1)[-1]))
            self._error(404, f"no route {path}")

        # -- the API surface ----------------------------------------------

        def _get_api(self, path: str, query: dict):
            if path == "/api/people":
                return api.people(query.get("q", ""))
            if path == "/api/places":
                return api.places(query.get("type", "country"),
                                  query.get("country", ""), query.get("state", ""))
            if path == "/api/albums":
                return api.albums()
            if path == "/api/groups":
                return api.groups()
            if path == "/api/trips":
                return api.trips(rescan=query.get("rescan") == "1",
                                 away_km=float(query.get("away_km") or 100),
                                 min_assets=int(query.get("min_assets") or 30),
                                 min_days=int(query.get("min_days") or 2))
            raise ApiError(f"no route {path}", status=404)

        def _post_api(self, path: str, body: dict):
            if path == "/api/preview":
                return api.preview(body)
            if path == "/api/analyze":
                return api.analyze(body)
            if path == "/api/albums":
                return api.save_album(body)
            if path == "/api/groups":
                return api.save_group(body.get("name", ""),
                                      body.get("members") or [])
            if path == "/api/run":
                return api.run(body, dry_run=bool(body.get("dry_run")))
            if path == "/api/add-assets":
                return api.add_assets(body)
            raise ApiError(f"no route {path}", status=404)

        def _call(self, work) -> None:
            try:
                self._json(work())
            except ApiError as exc:
                self._error(exc.status, str(exc))
            except ImmichError as exc:
                self._error(502, f"Immich: {exc}")
            except Exception as exc:                 # noqa: BLE001
                log.exception("design mode request failed")
                self._error(500, f"unexpected error: {exc}")

        # -- bodies and files ---------------------------------------------

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4_000_000:
                raise ValueError("request body too large")
            if not length:
                return {}
            try:
                data = json.loads(self.rfile.read(length))
            except json.JSONDecodeError as exc:
                raise ValueError(f"body is not JSON: {exc}") from None
            if not isinstance(data, dict):
                raise ValueError("body must be a JSON object")
            return data

        def _static(self, name: str) -> None:
            path = (STATIC / name).resolve()
            if not path.is_file() or STATIC.resolve() not in path.parents:
                return self._error(404, f"no file {name}")
            self._send(200, path.read_bytes(),
                       CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
                       {"Cache-Control": "no-cache"})

        def _thumbnail(self, asset_id: str) -> None:
            """Proxied so the page never needs the Immich key."""
            try:
                body, content_type = api.client.thumbnail(asset_id)
            except ImmichError as exc:
                return self._error(exc.status or 502, str(exc))
            self._send(200, body, content_type,
                       {"Cache-Control": "private, max-age=3600"})

    return Handler
