"""Design mode, at both levels.

`ApiTests` drive the feature directly; `ServerTests` go over a real socket so
auth, routing and the thumbnail proxy are exercised as a browser would.
"""

import base64
import json
import re
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from immich_album_butler import config as config_module
from immich_album_butler import trips as trips_module
from immich_album_butler.design.api import ApiError, DesignApi
from immich_album_butler.design.server import _make_handler
from immich_album_butler.immich import ImmichClient

from .stub_immich import API_KEY, StubImmich, day, fake_id, make_asset

UUID_LIKE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                       r"[0-9a-f]{4}-[0-9a-f]{8,12}", re.I)

ALEX, SAM = "person-alex", "person-sam"
PEOPLE = [{"id": ALEX, "name": "Alex"}, {"id": SAM, "name": "Sam"}]

LIBRARY = [
    make_asset(1, when=day(2019, 7, 1), lat=41.9, lon=12.5,
               city="Rome", state="Lazio", country="Italy", people=(ALEX,)),
    make_asset(2, when=day(2019, 7, 2), lat=41.9, lon=12.5,
               city="Rome", state="Lazio", country="Italy", people=(ALEX, SAM)),
    make_asset(3, when=day(2019, 7, 3), people=(SAM,)),               # no GPS
    make_asset(4, when=day(2020, 1, 1), lat=40.4, lon=-3.7,
               city="Madrid", country="Spain", people=(ALEX,)),
]

CONFIG_TOML = """\
server = "http://immich.example.lan:2283"
auto-update-schedule = "daily 03:30"
timezone = "UTC"
"""

ITALY = {
    "name": "Italy 2019",
    "match": {"from": "2019-07-01", "to": "2019-07-31",
              "countries": ["Italy"], "include_unlocated": True},
}


class DesignTestCase(unittest.TestCase):
    """A temp config dir plus a stub Immich, torn down per test."""

    assets = LIBRARY

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.config_dir = self.root / "config"
        self.state_dir = self.root / "state"
        self.config_dir.mkdir()
        (self.config_dir / "config.toml").write_text(CONFIG_TOML, encoding="utf-8")

        self.stub = StubImmich(self.assets, people=PEOPLE, page_size=100)
        self.stub.__enter__()
        self.client = ImmichClient(self.stub.url, API_KEY)
        self.api = DesignApi(self.client, self.config_dir, self.state_dir)
        self.addCleanup(self._temp.cleanup)
        self.addCleanup(self.stub.__exit__, None, None, None)


class PickerTests(DesignTestCase):
    def test_people_are_listed_when_nothing_is_typed(self):
        names = [p["name"] for p in self.api.people()["people"]]
        self.assertEqual(names, ["Alex", "Sam"])

    def test_a_partial_name_still_offers_the_person(self):
        """The picker must help while a name is half-typed."""
        names = [p["name"] for p in self.api.people("Al")["people"]]
        self.assertEqual(names, ["Alex"])

    def test_places_come_from_the_library(self):
        self.assertEqual(self.api.places("country")["values"], ["Italy", "Spain"])
        self.assertEqual(self.api.places("city")["values"], ["Madrid", "Rome"])

    def test_an_unknown_place_type_is_refused(self):
        with self.assertRaises(ApiError):
            self.api.places("planet")


class PreviewTests(DesignTestCase):
    def test_a_preview_counts_without_creating_anything(self):
        result = self.api.preview(ITALY)
        self.assertEqual(result["matched"], 3)       # two in Rome, one without GPS
        self.assertTrue(result["creates_album"])
        self.assertEqual(self.stub.albums, {})

    def test_dropping_unlocated_photos_shows_up_immediately(self):
        payload = {**ITALY, "match": {**ITALY["match"], "include_unlocated": False}}
        self.assertEqual(self.api.preview(payload)["matched"], 2)

    def test_it_reports_what_is_already_in_the_album(self):
        album = self.stub.add_album("Italy 2019", [fake_id(1)])
        result = self.api.preview(ITALY)
        self.assertEqual(result["already_in_album"], 1)
        self.assertEqual(result["to_add"], 2)
        self.assertFalse(result["creates_album"])
        self.assertIn(album["id"], self.stub.albums)

    def test_an_empty_rule_is_refused_rather_than_matching_everything(self):
        with self.assertRaises(ApiError) as caught:
            self.api.preview({"name": "Everything", "match": {}})
        self.assertIn("whole library", str(caught.exception))

    def test_a_backwards_date_range_names_the_problem(self):
        with self.assertRaises(ApiError) as caught:
            self.api.preview({"name": "Backwards",
                              "match": {"from": "2019-07-31", "to": "2019-07-01"}})
        self.assertIn("after", str(caught.exception))

    def test_an_unknown_person_is_reported_not_silently_dropped(self):
        with self.assertRaises(ApiError) as caught:
            self.api.preview({"name": "Who?", "match": {"people": ["Nobody"]}})
        self.assertIn("Nobody", str(caught.exception))

    def test_the_preview_says_when_the_album_would_next_run(self):
        self.assertIsNotNone(self.api.preview(ITALY)["next_run"])

    def test_a_manual_album_never_runs_automatically(self):
        payload = {**ITALY, "auto-update-schedule": "manual"}
        self.assertIsNone(self.api.preview(payload)["next_run"])

    def test_a_bad_schedule_is_refused_with_the_grammar(self):
        with self.assertRaises(ApiError) as caught:
            self.api.preview({**ITALY, "auto-update-schedule": "every other tuesday"})
        self.assertIn("daily HH:MM", str(caught.exception))


class SaveTests(DesignTestCase):
    def test_saving_writes_a_readable_toml_file(self):
        result = self.api.save_album(ITALY)
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn('name    = "Italy 2019"', text)
        self.assertIn("from = 2019-07-01", text)
        self.assertIn('countries = ["Italy"]', text)

    def test_a_saved_album_holds_no_uuid(self):
        """Configs are for people to read; ids live in state.json."""
        self.api.save_album({**ITALY, "match": {**ITALY["match"],
                                                "people": ["Alex"]}})
        self.api.run({"slug": "italy-2019"})
        text = (self.config_dir / "config.toml").read_text(encoding="utf-8")
        self.assertIn('people = ["Alex"]', text)
        self.assertNotIn(ALEX, text)                       # not the person id
        self.assertIsNone(UUID_LIKE.search(text))          # nor the album id
        self.assertIsNotNone(UUID_LIKE.search(
            (self.state_dir / "state.json").read_text(encoding="utf-8")))

    def test_an_inherited_schedule_is_written_as_a_comment(self):
        result = self.api.save_album(ITALY)
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn('# auto-update-schedule = "daily 03:30"', text)

    def test_an_explicit_schedule_is_written_as_a_value(self):
        result = self.api.save_album({**ITALY, "auto-update-schedule": "weekly sun 04:00"})
        text = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn('auto-update-schedule = "weekly sun 04:00"', text)
        self.assertNotIn("# auto-update", text)

    def test_a_saved_album_is_loaded_back_by_the_runtime_loader(self):
        """The real check: design mode's output must be runtime mode's input."""
        self.api.save_album(ITALY)
        config = config_module.load(self.config_dir)
        self.assertEqual([a.name for a in config.albums], ["Italy 2019"])
        self.assertEqual(config.albums[0].match.countries, ("Italy",))
        self.assertEqual(config.errors, [])

    def test_the_slug_comes_from_the_name(self):
        self.assertEqual(self.api.save_album(ITALY)["saved"], "italy-2019")

    def test_saving_again_overwrites_the_same_file(self):
        first = self.api.save_album(ITALY)
        second = self.api.save_album({**ITALY, "match": {**ITALY["match"],
                                                         "to": "2019-08-31"}})
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(len(config_module.load(self.config_dir).albums), 1)

    def test_a_person_album_needs_only_people(self):
        self.api.save_album({"name": "Photos of Alex", "match": {"people": ["Alex"]}})
        config = config_module.load(self.config_dir)
        self.assertEqual(config.albums[0].match.people, ("Alex",))

    def test_deleting_the_config_leaves_the_immich_album_alone(self):
        self.stub.add_album("Italy 2019", [fake_id(1)])
        self.api.save_album(ITALY)
        result = self.api.delete_album("italy-2019")
        self.assertEqual(result["deleted"], "italy-2019")
        self.assertEqual(config_module.load(self.config_dir).albums, [])
        self.assertIsNotNone(self.stub.album_named("Italy 2019"))

    def test_deleting_something_that_is_not_there_is_a_404(self):
        with self.assertRaises(ApiError) as caught:
            self.api.delete_album("nothing")
        self.assertEqual(caught.exception.status, 404)

    def test_the_album_list_reports_what_was_saved(self):
        self.api.save_album(ITALY)
        listed = self.api.albums()
        self.assertEqual(listed["albums"][0]["name"], "Italy 2019")
        self.assertTrue(listed["albums"][0]["schedule_inherited"])
        self.assertEqual(listed["default_schedule"], "daily 03:30")


class GroupTests(DesignTestCase):
    def test_a_group_is_saved_and_read_back(self):
        result = self.api.save_group("Family Example", ["Alex", "Sam"])
        self.assertEqual(result["groups"],
                         [{"name": "Family Example", "members": ["Alex", "Sam"]}])

    def test_an_album_may_refer_to_the_group_by_name(self):
        self.api.save_group("Family Example", ["Alex", "Sam"])
        self.api.save_album({"name": "Us", "match": {"people": ["Family Example"]}})
        config = config_module.load(self.config_dir)
        expanded, _ = config.expand_people(config.albums[0].match.people)
        self.assertEqual(expanded, ["Alex", "Sam"])

    def test_a_group_matching_its_members_photos(self):
        self.api.save_group("Family Example", ["Alex", "Sam"])
        result = self.api.preview({"name": "Us",
                                   "match": {"people": ["Family Example"]}})
        self.assertEqual(result["matched"], 4)      # the union of both people

    def test_a_group_cannot_contain_itself(self):
        with self.assertRaises(ApiError):
            self.api.save_group("Alex", ["Alex"])

    def test_an_empty_group_is_refused(self):
        with self.assertRaises(ApiError):
            self.api.save_group("Nobody", [])

    def test_a_group_still_in_use_is_not_deleted(self):
        self.api.save_group("Family Example", ["Alex", "Sam"])
        self.api.save_album({"name": "Us", "match": {"people": ["Family Example"]}})
        with self.assertRaises(ApiError) as caught:
            self.api.delete_group("Family Example")
        self.assertIn("Us", str(caught.exception))

    def test_an_unused_group_is_deleted(self):
        self.api.save_group("Family Example", ["Alex", "Sam"])
        self.assertEqual(self.api.delete_group("Family Example")["groups"], [])


class RunTests(DesignTestCase):
    def test_a_dry_run_changes_nothing(self):
        self.api.save_album(ITALY)
        result = self.api.run({"slug": "italy-2019"}, dry_run=True)
        self.assertEqual(result["added"], 3)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.stub.albums, {})

    def test_running_creates_the_album_and_remembers_it(self):
        self.api.save_album(ITALY)
        result = self.api.run({"slug": "italy-2019"})
        self.assertTrue(result["created"])
        self.assertEqual(result["added"], 3)
        self.assertIsNotNone(self.stub.album_named("Italy 2019"))
        self.assertIn("italy-2019", json.loads(
            (self.state_dir / "state.json").read_text())["albums"])

    def test_a_second_run_adds_nothing(self):
        self.api.save_album(ITALY)
        self.api.run({"slug": "italy-2019"})
        self.assertEqual(self.api.run({"slug": "italy-2019"})["added"], 0)

    def test_an_unsaved_draft_cannot_be_run(self):
        with self.assertRaises(ApiError) as caught:
            self.api.run({"slug": "never-saved"})
        self.assertEqual(caught.exception.status, 404)


class AnalyzeTests(DesignTestCase):
    def _scan(self):
        trips_module.save_scan(self.state_dir, trips_module.scan(self.client))
        self.api._scan = None

    def test_without_a_scan_it_says_so_instead_of_failing(self):
        result = self.api.analyze(ITALY)
        self.assertEqual(result["suggestions"], [])
        self.assertIn("scan", result["note"])

    def test_it_offers_the_unlocated_photos_a_place_rule_dropped(self):
        self._scan()
        payload = {**ITALY, "match": {**ITALY["match"], "include_unlocated": False}}
        keys = {s["key"] for s in self.api.analyze(payload)["suggestions"]}
        self.assertIn("window_unlocated", keys)

    def test_the_adjustment_it_offers_actually_changes_the_preview(self):
        """A suggestion is only useful if applying it does what it claims."""
        self._scan()
        payload = {**ITALY, "match": {**ITALY["match"], "include_unlocated": False}}
        before = self.api.preview(payload)["matched"]
        group = next(s for s in self.api.analyze(payload)["suggestions"]
                     if s["key"] == "window_unlocated")
        payload["match"].update(group["adjust"])
        self.assertEqual(self.api.preview(payload)["matched"],
                         before + group["count"])

    def test_adding_assets_needs_the_album_to_exist(self):
        with self.assertRaises(ApiError) as caught:
            self.api.add_assets({**ITALY, "asset_ids": [fake_id(3)]})
        self.assertIn("does not exist", str(caught.exception))

    def test_adding_assets_puts_them_in_the_album(self):
        self.api.save_album(ITALY)
        self.api.run({"slug": "italy-2019"})
        result = self.api.add_assets({**ITALY, "asset_ids": [fake_id(4)]})
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(self.stub.album_named("Italy 2019")["assets"]), 4)


class TripTests(DesignTestCase):
    # Far from the Rome "home" cluster, so it reads as a trip.
    assets = [make_asset(n, when=day(2019, 7, 1 + n // 10, 8 + n % 10),
                         lat=41.9, lon=12.5, city="Rome", country="Italy")
              for n in range(40)] + [
        make_asset(50 + n, when=day(2020, 3, 1 + n // 10, 8 + n % 10),
                   lat=35.7, lon=139.7, city="Tokyo", country="Japan")
        for n in range(40)]

    def test_no_scan_means_no_trips_rather_than_an_error(self):
        self.assertEqual(self.api.trips()["trips"], [])

    def test_a_scan_finds_the_trip_away_from_home(self):
        found = self.api.trips(rescan=True)["trips"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["countries"], ["Japan"])
        self.assertIn("Japan", found[0]["name"])

    def test_the_detected_trip_prefills_a_usable_rule(self):
        trip = self.api.trips(rescan=True)["trips"][0]
        result = self.api.preview({"name": trip["name"],
                                   "match": {"from": trip["start"], "to": trip["end"],
                                             "countries": trip["countries"],
                                             "include_unlocated": True}})
        self.assertEqual(result["matched"], trip["total"])

    def test_a_trip_already_covered_by_an_album_is_marked(self):
        trip = self.api.trips(rescan=True)["trips"][0]
        self.api.save_album({"name": "Japan", "match": {"from": trip["start"],
                                                        "to": trip["end"]}})
        self.assertEqual(self.api.trips()["trips"][0]["covered_by"], "Japan")

    def test_an_unrelated_album_does_not_mark_it(self):
        self.api.save_album({"name": "Other", "match": {"from": "2015-01-01",
                                                        "to": "2015-02-01"}})
        self.assertIsNone(self.api.trips(rescan=True)["trips"][0]["covered_by"])

    def test_the_second_call_uses_the_cache(self):
        self.api.trips(rescan=True)
        before = len(self.stub.requests)
        self.api.trips()
        searches = sum(1 for _, path in self.stub.requests[before:]
                       if path == "/api/search/metadata")
        self.assertEqual(searches, 0)


# --------------------------------------------------------------------------
# the HTTP layer
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# the HTTP layer, including the login
# --------------------------------------------------------------------------

USER, PASSWORD = "designer", "correct-horse-battery"  # private-data-check: allow


class ServerTestCase(DesignTestCase):
    """A real socket, with one configured login."""

    logins = True

    def setUp(self):
        super().setUp()
        from immich_album_butler.design.auth import (Sessions, Throttle, User,
                                                     Users, hash_password)
        from immich_album_butler.design.server import Idle

        self.accounts = Users([User(USER, hash_password(PASSWORD))]
                              if self.logins else [])
        self.sessions = Sessions()
        self.throttle = Throttle()
        handler = _make_handler(self.api, self.accounts, Idle(0),
                                self.sessions, self.throttle)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address[:2]
        self.host = f"{host}:{port}"
        self.base = f"http://{self.host}"
        self.cookie = None
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    # -- talking to it ----------------------------------------------------

    def fetch(self, path, *, method="GET", body=None, headers=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}{path}", data=data,
                                         method=method)
        request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            return (payload, response) if raw else json.loads(payload)

    def login(self, name=USER, password=PASSWORD, remember=False):
        """Post the sign-in form and keep the cookie, as a browser would."""
        fields = {"name": name, "password": password}
        if remember:
            fields["remember"] = "1"
        request = urllib.request.Request(
            f"{self.base}/login", data=urllib.parse.urlencode(fields).encode(),
            method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Origin", self.base)
        # The 303 is kept rather than followed, so its Set-Cookie can be read.
        # urllib raises on every non-2xx once redirects are suppressed, and the
        # exception *is* the response, so both paths end up in the same place.
        try:
            response = urllib.request.build_opener(_NoRedirect).open(
                request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        header = response.headers.get("Set-Cookie")
        if header:
            self.cookie = header.split(";")[0]
        return response

    def raw_get(self, path):
        """A request urllib would otherwise normalise before sending."""
        lines = [f"GET {path} HTTP/1.1", f"Host: {self.host}"]
        if self.cookie:
            lines.append(f"Cookie: {self.cookie}")
        lines += ["Connection: close", "", ""]
        with socket.create_connection(self.server.server_address[:2],
                                      timeout=10) as sock:
            sock.sendall("\r\n".join(lines).encode())
            chunks = []
            while chunk := sock.recv(65536):
                chunks.append(chunk)
        payload = b"".join(chunks)
        return int(payload.split(b" ", 2)[1]), payload


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keeps the 303 visible, so the Set-Cookie on it can be inspected."""

    def redirect_request(self, *args, **kwargs):
        return None


class LoginTests(ServerTestCase):
    def test_the_library_is_not_reachable_before_signing_in(self):
        """The point of the whole feature: no session, no photos."""
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch(f"/api/thumb/{fake_id(1)}")
        self.assertEqual(caught.exception.code, 401)

    def test_the_app_is_replaced_by_the_sign_in_page(self):
        body, response = self.fetch("/", raw=True)
        self.assertEqual(response.status, 200)
        self.assertIn(b"Sign in", body)
        self.assertNotIn(b'id="tabs"', body)

    def test_the_api_answers_401_rather_than_a_login_page(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/albums")
        self.assertEqual(caught.exception.code, 401)

    def test_signing_in_sets_an_http_only_same_site_cookie(self):
        header = self.login().headers.get("Set-Cookie")
        self.assertIn("butler_session=", header)
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)

    def test_after_signing_in_the_app_and_its_thumbnails_are_reachable(self):
        self.login()
        body, _ = self.fetch("/", raw=True)
        self.assertIn(b'id="tabs"', body)
        self.assertEqual(self.fetch("/api/albums")["albums"], [])
        thumb, _ = self.fetch(f"/api/thumb/{fake_id(1)}", raw=True)
        self.assertTrue(thumb.startswith(b"GIF89a"))

    def test_the_cookie_alone_is_enough_on_later_visits(self):
        """No repeated login: the browser sends the cookie and that is that."""
        self.login()
        for _ in range(3):
            self.assertEqual(self.fetch("/api/whoami")["user"], USER)

    def test_remember_me_outlives_the_browser_session(self):
        header = self.login(remember=True).headers.get("Set-Cookie")
        self.assertIn("Max-Age=2592000", header)        # 30 days

    def test_without_remember_me_the_cookie_lasts_the_working_day(self):
        header = self.login(remember=False).headers.get("Set-Cookie")
        self.assertIn("Max-Age=43200", header)          # 12 hours

    def test_a_wrong_password_does_not_sign_anyone_in(self):
        response = self.login(password="guess")  # private-data-check: allow
        self.assertEqual(response.status, 401)
        self.assertIsNone(response.headers.get("Set-Cookie"))

    def test_an_unknown_user_gets_the_same_message_as_a_wrong_password(self):
        """Which half was wrong is not the visitor's business."""
        wrong_user = self.login(name="nobody", password=PASSWORD).read()  # private-data-check: allow
        wrong_pass = self.login(name=USER, password="guess").read()  # private-data-check: allow
        self.assertIn(b"Wrong user name or password", wrong_user)
        self.assertIn(b"Wrong user name or password", wrong_pass)

    def test_the_user_name_is_not_case_sensitive(self):
        self.assertEqual(self.login(name=USER.upper()).status, 303)

    def test_a_forged_session_cookie_is_refused(self):
        self.cookie = "butler_session=made-up-value"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/albums")
        self.assertEqual(caught.exception.code, 401)

    def test_signing_out_invalidates_the_session(self):
        self.login()
        self.fetch("/logout", method="POST", body={}, raw=True)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/albums")
        self.assertEqual(caught.exception.code, 401)

    def test_an_expired_session_stops_working(self):
        self.login()
        for token in list(self.sessions._tokens):
            self.sessions._tokens[token] = (USER, 0.0)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/albums")
        self.assertEqual(caught.exception.code, 401)

    def test_repeated_guessing_is_locked_out(self):
        for _ in range(5):
            self.login(password="guess")  # private-data-check: allow
        response = self.login(password="guess")  # private-data-check: allow
        self.assertEqual(response.status, 429)
        self.assertIn(b"Too many failed attempts", response.read())

    def test_the_lockout_still_refuses_the_right_password(self):
        """Otherwise the throttle would be trivially side-stepped."""
        for _ in range(5):
            self.login(password="guess")  # private-data-check: allow
        self.assertEqual(self.login(password=PASSWORD).status, 429)  # private-data-check: allow

    def test_a_cross_origin_login_post_is_refused(self):
        request = urllib.request.Request(
            f"{self.base}/login",
            data=urllib.parse.urlencode({"name": USER,
                                         "password": PASSWORD}).encode(),
            method="POST")
        request.add_header("Origin", "http://evil.example.net")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 403)

    def test_the_sign_in_page_needs_its_stylesheet_before_sign_in(self):
        _, response = self.fetch("/static/style.css", raw=True)
        self.assertEqual(response.status, 200)

    def test_nothing_else_static_is_public(self):
        """The app itself is behind the login, even its script."""
        body, _ = self.fetch("/static/app.js", raw=True)
        self.assertNotIn(b"emptyDraft", body)
        self.assertIn(b"Sign in", body)


class RoutingTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_the_static_files_are_served(self):
        for path, marker in (("/static/app.js", b"emptyDraft"),
                             ("/static/style.css", b"--accent")):
            body, response = self.fetch(path, raw=True)
            self.assertEqual(response.status, 200)
            self.assertIn(marker, body)

    def test_a_path_outside_the_static_dir_is_not_served(self):
        """Sent over a raw socket: urllib would tidy the `..` away first."""
        for attempt in ("/static/../../../etc/passwd",
                        "/static/%2e%2e/%2e%2e/config.toml"):
            status, body = self.raw_get(attempt)
            self.assertEqual(status, 404, attempt)
            self.assertNotIn(b"server =", body)

    def test_the_preview_route_returns_counts(self):
        self.assertEqual(self.fetch("/api/preview", method="POST",
                                    body=ITALY)["matched"], 3)

    def test_an_api_error_comes_back_as_json_with_its_status(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/preview", method="POST",
                       body={"name": "Everything", "match": {}})
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("whole library",
                      json.loads(caught.exception.read())["error"])

    def test_an_unknown_route_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/nonsense")
        self.assertEqual(caught.exception.code, 404)

    def test_a_body_that_is_not_json_is_refused(self):
        request = urllib.request.Request(f"{self.base}/api/preview",
                                         data=b"not json", method="POST")
        request.add_header("Cookie", self.cookie)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 400)

    def test_a_cross_origin_write_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch("/api/albums", method="POST", body=ITALY,
                       headers={"Origin": "http://evil.example.net"})
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(config_module.load(self.config_dir).albums, [])

    def test_a_same_origin_write_is_allowed(self):
        result = self.fetch("/api/albums", method="POST", body=ITALY,
                            headers={"Origin": self.base})
        self.assertEqual(result["saved"], "italy-2019")

    def test_thumbnails_are_proxied_so_the_browser_never_sees_the_key(self):
        body, response = self.fetch(f"/api/thumb/{fake_id(1)}", raw=True)
        self.assertEqual(response.headers["Content-Type"], "image/gif")
        self.assertTrue(body.startswith(b"GIF89a"))
        self.assertNotIn(API_KEY.encode(), body)

    def test_a_missing_thumbnail_is_reported_not_crashed(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.fetch(f"/api/thumb/{fake_id(99)}")
        self.assertEqual(caught.exception.code, 404)

    def test_the_page_carries_a_restrictive_content_security_policy(self):
        _, response = self.fetch("/", raw=True)
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)


class UnprotectedTests(ServerTestCase):
    """Loopback with no [[design.users]]: allowed, and it says so."""

    logins = False

    def test_it_works_without_a_login(self):
        self.assertEqual(self.fetch("/api/albums")["albums"], [])

    def test_the_ui_is_told_there_is_no_login(self):
        self.assertFalse(self.fetch("/api/whoami")["protected"])


class BindingTests(unittest.TestCase):
    def test_binding_off_loopback_without_a_login_is_refused(self):
        from immich_album_butler.design.server import serve
        from immich_album_butler.immich import ImmichError
        with self.assertRaises(ImmichError) as caught:
            serve(None, host="0.0.0.0", port=0, users=[])
        self.assertIn("design.users", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
