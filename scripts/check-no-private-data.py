#!/usr/bin/env python3
"""Refuse to let secrets or personal data into this public repository.

Runs as a pre-commit hook and in CI. It looks for the things that would be
embarrassing or harmful to publish: private network addresses, Immich UUIDs,
key-like strings, email addresses, and any term the operator listed in a local
.private-terms file (their own names, hostnames and addresses -- kept out of
the repo itself, which is the whole point).

A line carrying the marker below is exempt. It exists so that this file's own
patterns, and the documentation of the rules, do not trip the check; it is not
a way to smuggle real data past it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOW_MARKER = "private-data-check" + ": allow"

TERMS_FILE = ".private-terms"

# Files we never scan: binary or generated, and the terms file itself.
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
             ".pytest_cache", ".ruff_cache", "build", "dist", "htmlcov"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
                 ".gz", ".xz", ".zst", ".woff", ".woff2", ".ttf", ".mo", ".db"}

# Placeholders that are safe by construction and must not be flagged.
ALLOWED_UUIDS = re.compile(r"^0{8}-0{4}-0{4}-0{4}-0{11}[0-9a-f]$", re.I)
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "example.lan")


def _allowed_email(address: str) -> bool:
    """Compare the domain itself -- 'notexample.net' must not pass as example.net."""
    local, _, domain = address.rpartition("@")
    domain = domain.lower()
    # No-reply addresses identify nobody; commit trailers are full of them.
    if local.lower() in ("noreply", "no-reply") or domain.endswith("noreply.github.com"):
        return True
    return any(domain == allowed or domain.endswith("." + allowed)
               for allowed in ALLOWED_EMAIL_DOMAINS)
# TEST-NET-1/2/3 and the documentation domain are reserved for exactly this use.
DOC_ADDRESSES = re.compile(r"^(?:192\.0\.2|198\.51\.100|203\.0\.113)\.")  # noqa

PRIVATE_IP = re.compile(                                  # private-data-check: allow
    r"\b(?:10(?:\.\d{1,3}){3}"                            # private-data-check: allow
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"         # private-data-check: allow
    r"|192\.168(?:\.\d{1,3}){2})\b")                      # private-data-check: allow

UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# An assignment that actually carries a value.        private-data-check: allow
# Empty values and obvious placeholders are fine -- .env.example needs them.
SECRET_ASSIGN = re.compile(
    r"\b(?:api[_-]?key|immich[_-]?key|ui[_-]?password|password|passwd|secret|token)\b"
    r"\s*[:=]\s*(?:[\"'](?P<quoted>[^\"']*)[\"']|(?P<bare>[^\s\"'#]+))", re.I)
PLACEHOLDER = re.compile(
    r"^(?:|<.*>|\{\{.*\}\}|\$\{?[A-Z_]+\}?|x{3,}|\.{3}|changeme|change-me|todo|none|null"
    r"|your[-_ ].*|example.*|placeholder.*|redacted.*|test[-_].*|fake.*|dummy.*|\*+"
    # A type annotation is not a secret.            private-data-check: allow
    r"|str|bytes|int|float|bool|Any|Optional\[.*\]|str \| None"
    # An f-string hole: the value is computed, so the source holds nothing.
    r"|\{[A-Za-z_][A-Za-z0-9_.\[\]'\"]*\})$", re.I)

# An all-zero scrypt hash, the password equivalent of the all-zero UUID: it is
# what the example config carries, and it matches no password. A hash elided
# with "..." counts too -- that is how the README shows the shape of one.
ZERO_HASH = re.compile(r"^scrypt\$\d+\$\d+\$\d+\$(?:A+=*\$A+=*|\.{3})$")

# `password = get_it()` is code, not a credential -- and so is `password=PASSWORD`  # private-data-check: allow
# as a keyword argument. A value that is a call, an attribute lookup, a bare
# variable reference or a bracketed expression is source; a real secret is a
# literal. Only ever applied to *unquoted* values (see scan_text).
CODE_VALUE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*\s*\("      # get_it(...)  /  self.thing(...)
    r"|^[A-Za-z_][A-Za-z0-9_]*\."         # obj.attr
    r"|^[(\[{]"                           # (form.get(...))[0]
    r"|^(?:\+\+|--)")                     # ++state.previewToken

# A bare identifier -- `password=PASSWORD` -- is a variable reference in source,  # private-data-check: allow
# but in a .env or a config file it is the secret itself (IMMICH_KEY=s3cr3t
# looks identical). So this one is allowed only where code lives.
BARE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CODE_SUFFIXES = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
                 ".c", ".h", ".cpp", ".sh"}

# A long, high-entropy-looking run of key characters sitting in the source.
KEYLIKE = re.compile(r"\b(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])"
                     r"(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{32,}\b")


@dataclass(frozen=True)
class Finding:
    path: str
    line_no: int
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_no}: {self.kind}: {self.detail}"


def load_terms(root: Path, terms_file: Path | None = None) -> list[str]:
    """Real-world names/hosts/addresses to reject, from a git-ignored local file."""
    path = terms_file if terms_file is not None else root / TERMS_FILE
    if not path.exists():
        return []
    terms = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        term = raw.strip()
        if term and not term.startswith("#"):
            terms.append(term)
    return terms


def _term_pattern(terms: list[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    # Word boundaries where the term starts/ends with a word character, so that
    # a name like "Ann" does not match inside "Announcement".
    parts = []
    for term in terms:
        esc = re.escape(term)
        left = r"\b" if term[:1].isalnum() else ""
        right = r"\b" if term[-1:].isalnum() else ""
        parts.append(f"{left}{esc}{right}")
    return re.compile("|".join(parts), re.I)


def scan_text(path: str, text: str, terms_re: re.Pattern[str] | None) -> list[Finding]:
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue

        for match in PRIVATE_IP.finditer(line):
            findings.append(Finding(path, line_no, "private IP address", match.group(0)))

        for match in UUID.finditer(line):
            if not ALLOWED_UUIDS.match(match.group(0)):
                findings.append(Finding(path, line_no, "UUID", match.group(0)))

        for match in EMAIL.finditer(line):
            if not _allowed_email(match.group(0)):
                findings.append(Finding(path, line_no, "email address", match.group(0)))

        for match in SECRET_ASSIGN.finditer(line):
            # A quoted value may contain spaces, e.g. "<your key here>".
            quoted = match.group("quoted")
            value = (quoted if quoted is not None
                     else match.group("bare")).rstrip(",);:")
            # The code exemption applies only to an unquoted value: an
            # expression is never in quotes, but "s3cret(value)" is a literal
            # that happens to look like a call, and must still be caught.
            code = quoted is None and (
                CODE_VALUE.match(value)
                or (BARE_NAME.match(value)
                    and Path(path).suffix.lower() in CODE_SUFFIXES))
            if not PLACEHOLDER.match(value) and not ZERO_HASH.match(value) and not code:
                findings.append(Finding(path, line_no, "secret assignment",
                                        f"{match.group(0)[:40]}..."))

        for match in KEYLIKE.finditer(line):
            findings.append(Finding(path, line_no, "key-like string",
                                    match.group(0)[:12] + "..."))

        if terms_re is not None:
            for match in terms_re.finditer(line):
                # Never print the term itself -- that would put it in the log.
                findings.append(Finding(path, line_no, "private term",
                                        f"{len(match.group(0))} characters, redacted"))
    return findings


def tracked_files(root: Path) -> list[Path]:
    """Prefer what git would publish; fall back to a plain walk."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                             capture_output=True, text=True, check=True).stdout
        names = [n for n in out.split("\0") if n]
        if names:
            return [root / n for n in names]
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    files = []
    for path in root.rglob("*"):
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
            files.append(path)
    return files


def check_tree(root: Path, terms_file: Path | None = None) -> list[Finding]:
    terms_re = _term_pattern(load_terms(root, terms_file))
    # The term list holds the very words we are looking for, so scanning it
    # would report every one of them.
    skip = {(root / TERMS_FILE).resolve()}
    if terms_file is not None:
        skip.add(terms_file.resolve())

    findings: list[Finding] = []
    for path in tracked_files(root):
        if path.suffix.lower() in SKIP_SUFFIXES or path.resolve() in skip:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        rel = path.relative_to(root).as_posix()
        findings.extend(scan_text(rel, text, terms_re))
    return findings


def check_message(root: Path, message_file: Path,
                  terms_file: Path | None = None) -> list[Finding]:
    terms_re = _term_pattern(load_terms(root, terms_file))
    text = message_file.read_text(encoding="utf-8")
    return scan_text("<commit message>", text, terms_re)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1],
                        help="repository root to scan (default: this repo)")
    parser.add_argument("--terms", type=Path, default=None,
                        help=f"path to the term list (default: <root>/{TERMS_FILE})")
    parser.add_argument("--commit-msg", type=Path, default=None,
                        help="scan this commit-message file instead of the tree")
    args = parser.parse_args(argv)

    if args.commit_msg is not None:
        findings = check_message(args.root, args.commit_msg, args.terms)
    else:
        findings = check_tree(args.root, args.terms)

    if not findings:
        return 0

    print("Refusing: this would publish private data.", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(f"\n{len(findings)} finding(s). Replace them with fictional data "
          f"(see CLAUDE.md), or mark a false positive with the allow marker.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
