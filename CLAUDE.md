# immich-album-butler — operating rules

## This is a PUBLIC repository

Everything committed here is world-readable, permanently. **Nothing secret and
nothing personal may ever enter this repo** — not in code, tests, fixtures,
examples, docs, screenshots or commit messages.

Never commit:

- **Secrets** — API keys, passwords, tokens, session cookies, `.env` files.
- **Personal data** — real people's names, family or group names, real album
  names, birth dates, email addresses, faces or photos.
- **Instance data** — Immich UUIDs (asset, person, album, library, owner),
  real file paths from a photo library, real trips, places or dates taken from
  a live instance.
- **Home-lab specifics** — RFC1918 addresses (`10.x`, `172.16–31.x`,
  `192.168.x`), hostnames, container/VM ids, the Proxmox host, SSH details.

Use **obviously fictional data** in examples and tests instead:

| Purpose | Use |
|---|---|
| People | `Alex`, `Sam`, `Robin` |
| Group | `Family Example` |
| Album / trip | `Italy 2019`, `Photos of Alex` |
| Server | `http://immich.example.lan:2283`, or `192.0.2.10` (TEST-NET-1) |
| UUID | `00000000-0000-0000-0000-000000000000` (and `…-0001`, `…-0002`) |
| Email | `someone@example.com` |

## The checker is not optional

`scripts/check-no-private-data.py` runs as a pre-commit hook and in CI. It
flags private IPs, non-zero UUIDs, key-like strings, email addresses, and any
term listed in `.private-terms`.

`.private-terms` is a **local, git-ignored** file holding the real names,
hostnames and addresses of the operator's own instance, so that they are
recognised and rejected without ever being written into the repo. Add to it;
never commit it. Install the hook with `scripts/install-hooks.sh`.

A line may be exempted with the marker `private-data-check: allow`, which is
meant for the checker's own patterns and for documentation of the rules. Do not
use it to smuggle real data past the check.

## Portability

The tool must stay generic: no default that only makes sense on one site. The
server URL, the time zone and every path come from configuration or the command
line. It targets **Python 3.11+ using the standard library only** (`tomllib`,
`urllib`, `http.server`, `zoneinfo`) — no third-party runtime dependencies, so
it can be unpacked onto a minimal host that has no pip.

## Safety toward Immich

The tool writes **albums only**. It never deletes assets, never modifies
originals, and never deletes an album. Removing assets *from* an album happens
only for an album explicitly configured with `sync = "mirror"`.
