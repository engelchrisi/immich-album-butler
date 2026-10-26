# Deployment

The three client projects use the same deployment engine. Run it from a
workstation checkout to update the application directly in its Linux systemd
container over SSH. The Proxmox host is not involved.

## Requirements

The workstation needs Python 3.11 or newer, Git, SSH and SCP. The target needs
Python 3.11 or newer and systemd, and the SSH account must be root because the
installer writes below `/opt`, `/etc` and `/etc/systemd/system`.

The maintained entry points are:

- `deploy/deploy.ps1` on Windows or PowerShell
- `deploy/deploy.sh` on Unix-like systems
- `deploy/deploy.py` underneath both wrappers

`deploy/install.sh` is the remote installer called by the engine. It can still
be run by hand from an unpacked checkout for a first installation.

## Normal deployment

PowerShell:

```powershell
.\deploy\deploy.ps1 root@example-host
```

Shell:

```sh
sh deploy/deploy.sh root@example-host
```

This deploys committed `HEAD`. It does not include uncommitted application
changes, which keeps routine releases tied to a known commit.

The engine builds a release archive, records its commit, version, frontend
provenance and SHA-256 hash for every file, then uploads it to a unique path in
`/tmp`. The remote side checks the archive and its paths, takes a per-project
deployment lock, saves the currently installed code and units, runs
`install.sh`, restarts the required services, and verifies the installed
version and readiness. A failed install or readiness check restores the former
code and systemd units and returns the services to their previous state.

Configuration, environment files and application state are outside the files
being replaced, so a deployment keeps them intact.

## Deploying local tracked changes

Use working-tree mode for an intentional test deployment:

```powershell
.\deploy\deploy.ps1 root@example-host -WorkingTree
```

```sh
sh deploy/deploy.sh root@example-host --working-tree
```

This includes modifications and deletions of tracked files plus staged new
files. Untracked files are excluded. Stage a new source file with `git add`
before using this mode, or commit it and use the normal deployment.

## Project-specific options

Album Butler has an optional design service. A normal deployment always starts
the runtime and resumes design mode only if it was already running. To
explicitly start design mode:

```powershell
.\deploy\deploy.ps1 root@example-host -DesignOnly
```

```sh
sh deploy/deploy.sh root@example-host --design-only
```

PyImmichFrame normally bundles the local `frontend/` directory and requires
both `frontend/index.html` and `frontend/BUILD.json`. To use another build:

```powershell
.\deploy\deploy.ps1 root@example-host -Frontend C:\builds\immich-frame
```

To update only the backend while deliberately retaining the installed
frontend:

```powershell
.\deploy\deploy.ps1 root@example-host -KeepFrontend
```

The shell equivalents are `--frontend PATH` and `--keep-frontend`. The other
two projects reject frontend options, and projects without a design service
reject `--design-only`.

## Building without deploying

Archive-only mode exercises packaging and validation without connecting to a
server:

```powershell
.\deploy\deploy.ps1 -Output release.tar.gz
```

```sh
sh deploy/deploy.sh --output release.tar.gz
```

The output path must not already exist. The archive contains
`DEPLOYMENT.json`, which records exactly what would be installed.

## Project checks

Each repository's `deploy/project.json` supplies the package name, systemd
services, configuration path and readiness endpoint:

| Project | Service readiness check | Special handling |
|---|---|---|
| immich-album-butler | stable runtime; `/login` when design mode runs | preserves optional design-service state |
| immich-dup-butler | `/healthz` | one service |
| PyImmichFrame | `/api/rev` | bundles or explicitly retains the frontend |

Run the repository test suite before deploying:

```powershell
python scripts/run-tests.py
```
