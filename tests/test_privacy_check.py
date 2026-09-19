"""The checker that keeps this public repo clean must itself be trustworthy.

Both halves are tested: that it catches planted private data, and that it does
not cry wolf over the fictional data the repo is full of.
"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check-no-private-data.py"

# The hyphenated file name is not importable, so load it by path. It has to be
# registered in sys.modules before executing: @dataclass looks its own module
# up there while building the class.
spec = importlib.util.spec_from_file_location("privacy_check", SCRIPT)
privacy = importlib.util.module_from_spec(spec)
sys.modules["privacy_check"] = privacy
spec.loader.exec_module(privacy)


class Tree:
    """A throwaway directory to plant things in."""

    def __init__(self, files: dict[str, str], terms: str | None = None):
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name)
        for name, text in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.terms_file = None
        if terms is not None:
            self.terms_file = self.path / "terms.txt"
            self.terms_file.write_text(terms, encoding="utf-8")

    def check(self):
        return privacy.check_tree(self.path, self.terms_file)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._temp.cleanup()


class CatchesTests(unittest.TestCase):
    def test_a_private_address_is_caught(self):
        for address in ["192.168.1.119", "10.0.0.5", "172.20.1.1"]:  # private-data-check: allow
            with self.subTest(address=address):
                with Tree({"a.py": f'SERVER = "http://{address}:2283"\n'}) as tree:
                    findings = tree.check()
                self.assertEqual(len(findings), 1)
                self.assertEqual(findings[0].kind, "private IP address")

    def test_a_real_uuid_is_caught(self):
        with Tree({"a.py": 'ID = "b4cbd65a-5920-4f6e-85a5-6f58af0f0633"\n'}) as tree:  # private-data-check: allow
            findings = tree.check()
        self.assertEqual(findings[0].kind, "UUID")

    def test_an_email_address_is_caught(self):
        # Note the domain only *ends with* an allowed one, which must not pass.
        with Tree({"a.md": "write to someone@notexample.net\n"}) as tree:  # private-data-check: allow
            self.assertEqual(tree.check()[0].kind, "email address")

    def test_a_filled_in_secret_is_caught(self):
        with Tree({".env.sample": "IMMICH_KEY=s3cr3tvalue\n"}) as tree:  # private-data-check: allow
            self.assertEqual(tree.check()[0].kind, "secret assignment")

    def test_a_long_random_looking_string_is_caught(self):
        with Tree({"a.py": 'KEY = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5bC7d"\n'}) as tree:  # private-data-check: allow
            self.assertEqual(tree.check()[0].kind, "key-like string")

    def test_a_private_term_is_caught_without_being_echoed(self):
        with Tree({"a.md": "The Hauptstrasse box runs it.\n"},
                  terms="Hauptstrasse\n") as tree:
            findings = tree.check()
        self.assertEqual(findings[0].kind, "private term")
        self.assertNotIn("Hauptstrasse", str(findings[0]))

    def test_private_terms_match_regardless_of_case(self):
        with Tree({"a.md": "the HAUPTSTRASSE box\n"}, terms="Hauptstrasse\n") as tree:
            self.assertEqual(len(tree.check()), 1)

    def test_every_finding_names_the_file_and_line(self):
        with Tree({"deep/a.py": "x = 1\ny = '192.168.1.5'\n"}) as tree:  # private-data-check: allow
            finding = tree.check()[0]
        self.assertEqual(finding.path, "deep/a.py")
        self.assertEqual(finding.line_no, 2)


class AllowsTests(unittest.TestCase):
    def test_the_documentation_placeholders_pass(self):
        text = ('server = "http://immich.example.lan:2283"\n'
                'other = "192.0.2.10"\n'
                'id = "00000000-0000-0000-0000-000000000000"\n'
                'mail = "someone@example.com"\n'
                "IMMICH_KEY=\n"  # private-data-check: allow
                "UI_PASSWORD=changeme\n"  # private-data-check: allow
                'API_KEY="<your key here>"\n')  # private-data-check: allow
        with Tree({"examples/config.toml": text}) as tree:
            self.assertEqual(tree.check(), [])

    def test_numbered_placeholder_uuids_pass(self):
        with Tree({"a.py": 'ID = "00000000-0000-0000-0000-000000000007"\n'}) as tree:
            self.assertEqual(tree.check(), [])

    def test_a_public_address_is_not_private(self):
        with Tree({"a.py": 'DNS = "8.8.8.8"\n'}) as tree:
            self.assertEqual(tree.check(), [])

    def test_the_allow_marker_exempts_a_line(self):
        marker = "private-data-check" + ": allow"
        with Tree({"a.py": f'IP = "192.168.1.1"   # {marker}\n'}) as tree:  # private-data-check: allow
            self.assertEqual(tree.check(), [])

    def test_ordinary_long_identifiers_are_not_mistaken_for_keys(self):
        with Tree({"a.py": "def resolve_people_for_this_album_rule(): pass\n"}) as tree:
            self.assertEqual(tree.check(), [])

    def test_binary_and_image_files_are_skipped(self):
        with Tree({"logo.png": "192.168.1.1"}) as tree:  # private-data-check: allow
            self.assertEqual(tree.check(), [])

    def test_the_terms_file_itself_is_never_scanned(self):
        with Tree({privacy.TERMS_FILE: "192.168.1.1\n"}) as tree:  # private-data-check: allow
            self.assertEqual(tree.check(), [])

    def test_no_terms_file_is_fine(self):
        with Tree({"a.py": "x = 1\n"}) as tree:
            self.assertEqual(tree.check(), [])


class CommitMessageTests(unittest.TestCase):
    def test_a_commit_message_is_checked_too(self):
        with Tree({}) as tree:
            message = tree.path / "msg.txt"
            message.write_text("fix the box at 192.168.1.119\n", encoding="utf-8")  # private-data-check: allow
            findings = privacy.check_message(tree.path, message)
        self.assertEqual(findings[0].kind, "private IP address")

    def test_a_no_reply_trailer_address_passes(self):
        """Commit trailers carry no-reply addresses, which identify nobody."""
        with Tree({}) as tree:
            message = tree.path / "msg.txt"
            message.write_text("Add trips\n\nCo-Authored-By: Someone "
                               "<noreply@somewhere.example>\n", encoding="utf-8")
            self.assertEqual(privacy.check_message(tree.path, message), [])

    def test_a_clean_commit_message_passes(self):
        with Tree({}) as tree:
            message = tree.path / "msg.txt"
            message.write_text("Add trip detection\n", encoding="utf-8")
            self.assertEqual(privacy.check_message(tree.path, message), [])


class ExitCodeTests(unittest.TestCase):
    def test_a_finding_exits_non_zero_and_explains(self):
        with Tree({"a.py": 'IP = "192.168.1.1"\n'}) as tree:  # private-data-check: allow
            result = subprocess.run(
                ["python", str(SCRIPT), "--root", str(tree.path)],
                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("private data", result.stderr)

    def test_this_repository_itself_is_clean(self):
        """The real check, run against the real tree -- the point of all this."""
        result = subprocess.run(["python", str(SCRIPT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
