"""The login primitives.

These guard the photo library, so they get tested for the properties that
matter -- salting, rejection of anything that is not a hash, lockout -- not
just the happy path.
"""

import time
import unittest

from immich_album_butler.design.auth import (AuthError, Sessions, Throttle, User,
                                             Users, hash_password,
                                             verify_password)

PASSWORD = "correct-horse-battery"  # private-data-check: allow


class HashTests(unittest.TestCase):
    def test_a_password_verifies_against_its_own_hash(self):
        self.assertTrue(verify_password(hash_password(PASSWORD), PASSWORD))

    def test_a_wrong_password_does_not(self):
        self.assertFalse(verify_password(hash_password(PASSWORD), "guess"))

    def test_the_password_itself_is_nowhere_in_the_hash(self):
        self.assertNotIn(PASSWORD, hash_password(PASSWORD))

    def test_the_same_password_hashes_differently_every_time(self):
        """Salted: two identical passwords must not look identical on disk."""
        self.assertNotEqual(hash_password(PASSWORD), hash_password(PASSWORD))

    def test_a_short_password_is_refused_when_it_is_set(self):
        with self.assertRaises(AuthError):
            hash_password("short")

    def test_an_empty_password_is_refused(self):
        with self.assertRaises(AuthError):
            hash_password("")

    def test_an_empty_guess_never_passes(self):
        self.assertFalse(verify_password(hash_password(PASSWORD), ""))

    def test_a_plaintext_password_in_the_hash_field_never_verifies(self):
        """A config holding a bare password must not accidentally work."""
        self.assertFalse(verify_password(PASSWORD, PASSWORD))

    def test_a_corrupt_hash_is_refused_rather_than_crashing(self):
        for broken in ("", "scrypt$", "scrypt$x$y$z$a$b", "bcrypt$1$1$1$aa$bb",
                       "scrypt$32768$8$1$not-base64$also-not"):
            self.assertFalse(verify_password(broken, PASSWORD), broken)


class UsersTests(unittest.TestCase):
    def setUp(self):
        self.users = Users([User("designer", hash_password(PASSWORD))])

    def test_the_right_credentials_return_the_user(self):
        self.assertEqual(self.users.check("designer", PASSWORD).name, "designer")

    def test_the_name_is_not_case_sensitive(self):
        self.assertIsNotNone(self.users.check("DESIGNER", PASSWORD))

    def test_a_wrong_password_returns_nobody(self):
        self.assertIsNone(self.users.check("designer", "guess"))

    def test_an_unknown_user_returns_nobody(self):
        self.assertIsNone(self.users.check("nobody", PASSWORD))

    def test_no_configured_users_means_nobody_can_sign_in(self):
        self.assertIsNone(Users([]).check("designer", PASSWORD))
        self.assertEqual(len(Users([])), 0)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.user = User("designer", hash_password(PASSWORD))
        self.sessions = Sessions()

    def test_a_fresh_token_names_its_user(self):
        token = self.sessions.create(self.user)
        self.assertEqual(self.sessions.user_for(token), "designer")

    def test_tokens_are_not_guessable_or_repeated(self):
        tokens = {self.sessions.create(self.user) for _ in range(20)}
        self.assertEqual(len(tokens), 20)
        self.assertTrue(all(len(t) >= 32 for t in tokens))

    def test_an_unknown_token_names_nobody(self):
        self.sessions.create(self.user)
        self.assertIsNone(self.sessions.user_for("made-up"))

    def test_no_token_names_nobody(self):
        self.assertIsNone(self.sessions.user_for(None))
        self.assertIsNone(self.sessions.user_for(""))

    def test_dropping_a_token_ends_that_session(self):
        token = self.sessions.create(self.user)
        self.sessions.drop(token)
        self.assertIsNone(self.sessions.user_for(token))

    def test_an_expired_token_stops_working(self):
        self.sessions.hours = 0
        token = self.sessions.create(self.user)
        time.sleep(0.01)
        self.assertIsNone(self.sessions.user_for(token))

    def test_one_session_ending_leaves_the_others_alone(self):
        first = self.sessions.create(self.user)
        second = self.sessions.create(self.user)
        self.sessions.drop(first)
        self.assertEqual(self.sessions.user_for(second), "designer")


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.throttle = Throttle(max_attempts=3, lockout=60)

    def test_a_few_failures_are_tolerated(self):
        self.throttle.failed("user:designer")
        self.assertEqual(self.throttle.locked_for("user:designer"), 0.0)

    def test_enough_failures_lock_the_account(self):
        for _ in range(3):
            self.throttle.failed("user:designer")
        self.assertGreater(self.throttle.locked_for("user:designer"), 0)

    def test_a_success_clears_the_count(self):
        self.throttle.failed("user:designer")
        self.throttle.failed("user:designer")
        self.throttle.passed("user:designer")
        for _ in range(2):
            self.throttle.failed("user:designer")
        self.assertEqual(self.throttle.locked_for("user:designer"), 0.0)

    def test_the_lockout_expires(self):
        throttle = Throttle(max_attempts=1, lockout=0.01)
        throttle.failed("user:designer")
        time.sleep(0.05)
        self.assertEqual(throttle.locked_for("user:designer"), 0.0)

    def test_locking_one_key_leaves_another_alone(self):
        for _ in range(3):
            self.throttle.failed("user:designer")
        self.assertEqual(self.throttle.locked_for("host:192.0.2.1"), 0.0)


if __name__ == "__main__":
    unittest.main()
