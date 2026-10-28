+# Deployment runbook

This runbook applies to PyImmichFrame, Immich Album Butler and Immich Dup
Butler. Their verified deployment engine is intentionally identical.

## Success criteria

A deployment is complete only when the command prints both:

```text
Verified <project> <version> (<commit>).
Deployment complete.
```

Before those lines, the engine has not declared success. It verifies the
release manifest, installs under a per-project lock, restarts the intended
services, probes configured HTTP health endpoints, and requires the service
identity to remain stable for three seconds. Configuration, environment files
and application state are preserved.

## Before deploying

1. Work from the intended repository and branch.
2. Review `git status --short --branch`. Do not accidentally deploy unrelated
   tracked edits.
3. Run the complete project suite:

   ```powershell
   python scripts/run-tests.py
   ```

4. Decide which source is being released:

   - Normal deployment uses committed `HEAD`.
   - `-WorkingTree` / `--working-tree` is only for an intentional test
     release. It includes tracked modifications and deletions plus staged
     additions, but excludes untracked files.
   - For PyImmichFrame, decide whether to replace the frontend (default), name
     another frontend build, or deliberately keep the installed one.

5. Unlock the SSH key agent before starting. A successful deployment uses one
   SSH connection and therefore one key signature.

An archive-only build exercises collection, syntax checks, manifest generation
and hashing without contacting a server:

```powershell
.\deploy\deploy.ps1 -Output release.tar.gz
```

```sh
sh deploy/deploy.sh --output release.tar.gz
```

Use a new output path; the command refuses to overwrite one.

## Deploy

PowerShell:

```powershell
.\deploy\deploy.ps1 root@example-host
```

Intentional tracked working-tree release:

```powershell
.\deploy\deploy.ps1 root@example-host -WorkingTree
```

Shell equivalents:

```sh
sh deploy/deploy.sh root@example-host
sh deploy/deploy.sh root@example-host --working-tree
```

Album Butler preserves whether its optional design service was running. Use
`-DesignOnly` / `--design-only` only when design mode is explicitly wanted.

Do not run another deployment in parallel. The remote lock refuses it, but a
second command adds noise while diagnosing the first.

## Read the output by phase

| Observed output | Phase | Meaning and action |
|---|---|---|
| No `Prepared ...` line | Local packaging | Fix the reported source, frontend, Git or manifest problem. Nothing reached the server. |
| `Prepared ...`, then a pause | SSH authentication/streaming | Check the key agent first. If authentication succeeded, allow time for the archive stream before troubleshooting systemd. |
| Installer output | Remote preflight/install | The server is validating or replacing the release. Let the engine finish. |
| `Deployment failed; restoring previous code and units.` | Automatic rollback | Wait for rollback verification. Do not start a competing manual restart. |
| `Rollback needs attention; backup retained at ...` | Manual recovery required | Preserve the named backup and inspect services, units and journal before changing files. |
| `Verified ...` then `Deployment complete.` | Success | Code, requested services and readiness checks passed. |

On a LAN, an ordinary update should usually finish in seconds to well under a
minute; the three-second health-stability period is deliberate. A long pause
immediately after `Prepared` is normally authentication, not upload,
installation or readiness.

## SSH-agent failures

A key can be listed by the agent and accepted by the server, yet still fail
when the agent is asked to sign. Typical output is:

```text
sign_and_send_pubkey: signing failed ... communication with agent failed
```

That failure occurs before the release is installed.

1. Open and unlock the key manager.
2. Confirm its SSH-agent integration is enabled.
3. Test one non-interactive connection:

   ```sh
   ssh -o BatchMode=yes root@example-host true
   ```

4. Retry the deployment only after that succeeds.

Do not infer that a slow signing prompt is a slow installer. The tuned engine
streams the helper and archive through one SSH session; successful deployments
no longer authenticate separately for upload, install and cleanup.

## Configuration changes

Deployments deliberately preserve the live config, environment file and state.
Shipping a new example or default does not add explicit keys to an existing
config.

When an operational change also needs a live config edit:

1. Read only the relevant table; never print the complete config or environment
   file into a terminal transcript.
2. Save a same-host backup before editing.
3. Make the smallest possible change, preferably atomically.
4. Parse the edited file or run the project's documented check command before
   restarting.
5. Restart only when the setting requires it. Use hot reload only when the
   project documents support for it.
6. Verify service state and its health endpoint.
7. Record the config keys changed, but never their secrets.

A code rollback does not roll back config or state. If a config edit caused the
incident, restore its separate backup deliberately.

## Independent verification

The deployment command already performs authoritative version and readiness
checks. Extra checks are useful when diagnosing, but should not contradict the
engine because they ran from the wrong directory.

Python puts the current directory before `PYTHONPATH`. A stale package under
the SSH user's home can therefore produce a false version. Always change to
the installation prefix first:

```sh
cd /opt/<project>
PYTHONPATH=/opt/<project> python3 -c \
  "import <package>; print(<package>.__file__); print(<package>.__version__)"
```

Then check the intended units and project endpoint:

| Project | Installation/package | Services | Readiness |
|---|---|---|---|
| PyImmichFrame | `/opt/pyimmichframe`; `pyimmichframe` | `pyimmichframe` | `/api/rev` |
| Immich Album Butler | `/opt/immich-album-butler`; `immich_album_butler` | `immich-album-butler`; optional `immich-album-butler-design` | runtime stability; design `/login` |
| Immich Dup Butler | `/opt/immich-dup-butler`; `immich_dup_butler` | `immich-dup-butler` | `/healthz` |

Immediately after a separate manual restart, an HTTP connection can be refused
for a moment even though systemd says `active`. Retry the endpoint briefly
before declaring failure. During a normal deployment the engine already does
this polling for up to 30 seconds.

## Recovery

The engine backs up application code, deployment metadata, frontend when
applicable, and systemd units before replacement. On install or readiness
failure it stops the attempted services, restores those paths, reloads systemd,
returns previously active services to their prior state, and verifies them.

If automatic rollback succeeds, diagnose the failed release locally and build a
new one. The command still ends with the original deployment error. When the
engine reaches that final error without printing `Rollback needs attention`,
rollback verification succeeded and the temporary backup was removed. Do not
copy individual files over the restored installation.

If rollback itself fails:

1. Do not delete the retained backup named in the error.
2. Capture `systemctl status` and the relevant journal.
3. Compare installed paths and units with the backup.
4. Restore only the affected project paths.
5. Reload systemd, start only the services that were active before, and verify
   readiness.
6. Record the exact manual actions.

## Deployment record

For each production deployment, retain:

- project, version and commit shown by `Prepared`/`Verified`;
- whether the source was `HEAD` or `tracked-working-tree`;
- frontend mode/build when applicable;
- start time and elapsed time;
- final service/readiness result;
- separate config keys changed;
- whether rollback ran and whether it verified.

Never include API keys, tokens, private configuration values or full
environment-file contents.

