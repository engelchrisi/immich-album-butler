#!/usr/bin/env python3
"""Shared deployment engine. Keep identical in the three client projects.

The shell and PowerShell entry points only forward arguments to this file.
The same file is sent over SSH on stdin for remote installation; the source
archive may therefore be an older release without this deployment engine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import uuid


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack(archive, destination):
    """Extract regular files only; never accept links or escaping paths."""
    seen = set()
    with tarfile.open(archive, "r:*") as tar:
        for item in tar:
            path = PurePosixPath(item.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.name
                    or not path.parts or ":" in item.name):
                raise ValueError(f"Unsafe archive path: {item.name}")
            if item.isdir():
                continue
            if not item.isfile() or path.as_posix() in seen:
                raise ValueError(f"Unsupported or repeated archive entry: {item.name}")
            seen.add(path.as_posix())
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(item) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(item.mode & 0o777)


def validate_settings(settings):
    for key in ("name", "package"):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", settings[key]):
            raise ValueError(f"Invalid project {key}")
    for service in settings["services"]:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", service):
            raise ValueError("Invalid service name")
    if settings["primary"] not in settings["services"]:
        raise ValueError("Primary service is missing")
    design = settings.get("design_service")
    if design is not None and design not in settings["services"]:
        raise ValueError("Design service is missing")


def source_files(stage):
    return {p.relative_to(stage).as_posix(): digest(p)
            for p in sorted(stage.rglob("*")) if p.is_file()}


def validate_source(stage, settings):
    required = ["pyproject.toml", "deploy/install.sh", "examples/config.toml",
                f"{settings['package']}/__init__.py"]
    required += [f"deploy/{s}.service" for s in settings["services"]]
    required += [f"deploy/{settings['name']}.env.example"]
    for name in required:
        if not (stage / name).is_file():
            raise ValueError(f"Snapshot is missing {name}")
    project = tomllib.loads((stage / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["name"].lower() != settings["name"]:
        raise ValueError("Snapshot belongs to a different project")
    for path in (stage / settings["package"]).rglob("*.py"):
        compile(path.read_bytes(), str(path), "exec")
    return project["version"]


def build_bundle(root, settings, work, *, working_tree=False, frontend=None,
                 keep_frontend=False, design_only=False):
    stage = work / "source"
    stage.mkdir()
    paths = [settings["package"], "deploy", "examples", "pyproject.toml"]
    commit = run("git", "rev-parse", "HEAD", cwd=root, capture_output=True,
                 text=True).stdout.strip()
    if working_tree:
        names = run("git", "ls-files", "--cached", "-z", "--", *paths,
                    cwd=root, capture_output=True).stdout.decode("utf-8").split("\0")
        for name in sorted(set(names) - {""}):
            source = root / name
            if source.is_symlink():
                raise ValueError(f"Symlinks cannot be deployed: {name}")
            if not source.is_file():
                continue  # A tracked deletion must stay deleted in the snapshot.
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    else:
        snapshot = work / "head.tar"
        run("git", "archive", "--format=tar", "--output", str(snapshot),
            commit, "--", *paths, cwd=root)
        unpack(snapshot, stage)
    # Normalize the files consumed by sh and systemd before hashing the release.
    for path in (stage / "deploy").rglob("*"):
        if path.is_file() and path.suffix in (".sh", ".service", ".example"):
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    for cache in stage.rglob("__pycache__"):
        if cache.is_dir():
            shutil.rmtree(cache)
    version = validate_source(stage, settings)
    frontend_mode = "unused"
    frontend_build = None
    if settings.get("frontend"):
        frontend_mode = "keep" if keep_frontend else "replace"
        if not keep_frontend:
            frontend = Path(frontend) if frontend else root / "frontend"
            if not (frontend / "index.html").is_file() or not (frontend / "BUILD.json").is_file():
                raise ValueError("Frontend needs index.html and BUILD.json; build it first, "
                                 "or explicitly use --keep-frontend for an existing installation")
            frontend_build = json.loads(
                (frontend / "BUILD.json").read_text(encoding="utf-8"))
            if any(p.is_symlink() for p in frontend.rglob("*")):
                raise ValueError("Frontend must not contain symlinks")
            shutil.copytree(frontend, stage / "frontend")
    manifest = {
        "schema": 1, "project": settings, "commit": commit, "version": version,
        "source": "tracked-working-tree" if working_tree else "HEAD",
        "frontend": frontend_mode, "frontend_build": frontend_build,
        "design_only": design_only,
        "files": source_files(stage),
    }
    (stage / "DEPLOYMENT.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive = work / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                item = tar.gettarinfo(str(path), path.relative_to(stage).as_posix())
                item.uid = item.gid = 0
                item.uname = item.gname = ""
                item.mode = 0o644
                with path.open("rb") as stream:
                    tar.addfile(item, stream)
    return archive, manifest


def active(service):
    return subprocess.run(["systemctl", "is-active", "--quiet", service]).returncode == 0


def service_plan(settings, manifest, was_active):
    """Choose requested services and always resume anything previously active."""
    if manifest["design_only"]:
        requested = [settings["design_service"]]
    else:
        requested = [settings["primary"]]
    return list(dict.fromkeys(requested + [
        service for service in settings["services"] if was_active[service]
    ]))


def service_identity(service):
    result = run("systemctl", "show", service, "--property=MainPID",
                 "--property=NRestarts", capture_output=True, text=True)
    return result.stdout


def health_url(settings, service):
    health = settings.get("health", {}).get(service)
    if not health:
        return None
    config = tomllib.loads(Path(settings["config"]).read_text(encoding="utf-8"))
    section = config.get(health["section"], {}) if health["section"] else config
    port = int(section.get(health["port_key"], health["default_port"]))
    host = section.get("host", "127.0.0.1") if health.get("use_host") else "127.0.0.1"
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}{health['path']}"


def verify_services(settings, services, timeout=30):
    """Require each unit to stay active without a restart, and probe its HTTP API."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    stable_since = None
    previous = None
    while time.monotonic() < deadline:
        identities = []
        ready = True
        for service in services:
            if not active(service):
                ready = False
                continue
            identities.append(service_identity(service))
            url = health_url(settings, service)
            if url:
                try:
                    with opener.open(url, timeout=2) as response:
                        ready = ready and response.status == 200
                except (OSError, urllib.error.URLError):
                    ready = False
        now = time.monotonic()
        if ready:
            if stable_since is None or identities != previous:
                stable_since = now
            if now - stable_since >= 3:
                return
        else:
            stable_since = None
        previous = identities
        time.sleep(0.5)
    raise RuntimeError("Service readiness verification failed: " + ", ".join(services))


def remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def save_paths(paths, backup):
    """Back up only code, metadata and units; configuration/state stay in place."""
    records = []
    for i, path in enumerate(paths):
        if path.is_symlink():
            raise ValueError(f"Refusing to replace a symlink: {path}")
        saved = backup / str(i)
        exists = path.exists()
        if path.is_dir():
            shutil.copytree(path, saved, symlinks=True)
        elif exists:
            shutil.copy2(path, saved)
        records.append((path, saved, exists))
    (backup / "paths.json").write_text(json.dumps(
        [{"path": str(p), "backup": str(s), "existed": e} for p, s, e in records], indent=2))
    return records


def restore_paths(records):
    for path, saved, existed in records:
        remove(path)
        if existed:
            path.parent.mkdir(parents=True, exist_ok=True)
            if saved.is_dir():
                shutil.copytree(saved, path, symlinks=True)
            else:
                shutil.copy2(saved, path)


def install_release(stage, manifest, *, opt=Path("/opt"), units=Path("/etc/systemd/system")):
    settings = manifest["project"]
    prefix = opt / settings["name"]
    if prefix.is_symlink():
        raise ValueError(f"Refusing a symlink installation prefix: {prefix}")
    if manifest["frontend"] == "keep" and not (prefix / "frontend/index.html").is_file():
        raise ValueError("--keep-frontend requires an existing installed frontend")
    # No config-writing application 'check' command or Immich mutation is run here.
    env = dict(os.environ, PYTHONPATH=str(stage), PYTHONDONTWRITEBYTECODE="1")
    code = ("import importlib; "
            f"print(importlib.import_module({settings['package']!r}).__version__)")
    version = run(sys.executable, "-c", code, cwd=stage, env=env,
                  capture_output=True, text=True).stdout.strip()
    if version != manifest["version"]:
        raise ValueError("Staged package version does not match its manifest")
    run("sh", "-n", str(stage / "deploy/install.sh"))
    was_active = {s: active(s) for s in settings["services"]}
    services = service_plan(settings, manifest, was_active)
    paths = [prefix / settings["package"], prefix / "pyproject.toml", prefix / "DEPLOYMENT.json"]
    if settings.get("frontend") and manifest["frontend"] == "replace":
        paths.append(prefix / "frontend")
    paths += [units / f"{s}.service" for s in settings["services"]]
    backup = Path(tempfile.mkdtemp(prefix=f".{settings['name']}-rollback-", dir=opt))
    retain_backup = False
    try:
        records = save_paths(paths, backup)
        try:
            # Stop all running users of the shared package while replacing it.
            # --design-only resumes the runtime too, if it was already running.
            for service, running in was_active.items():
                if running:
                    run("systemctl", "stop", service)
            install_env = dict(os.environ, PREFIX=str(prefix))
            for key in ("CONFIG_DIR", "STATE_DIR", "USER_NAME"):
                install_env.pop(key, None)
            run("sh", str(stage / "deploy/install.sh"), env=install_env)
            deployed = dict(manifest)
            if manifest["frontend"] == "keep":
                deployed["retained_frontend_files"] = source_files(prefix / "frontend")
                deployed["frontend_build"] = json.loads(
                    (prefix / "frontend/BUILD.json").read_text(encoding="utf-8"))
            (prefix / "DEPLOYMENT.json").write_text(json.dumps(deployed, indent=2) + "\n")
            installed_env = dict(env, PYTHONPATH=str(prefix))
            version = run(sys.executable, "-c", code, cwd=prefix, env=installed_env,
                          capture_output=True, text=True).stdout.strip()
            if version != manifest["version"]:
                raise RuntimeError("Installed version does not match the release")
            for service in services:
                run("systemctl", "start", service)
            verify_services(settings, services)
        except BaseException:
            print("Deployment failed; restoring previous code and units.", file=sys.stderr)
            try:
                for service in settings["services"]:
                    subprocess.run(["systemctl", "stop", service], check=False)
                restore_paths(records)
                run("systemctl", "daemon-reload")
                for service, running in was_active.items():
                    if running:
                        run("systemctl", "start", service)
                previous = [s for s, running in was_active.items() if running]
                if previous:
                    verify_services(settings, previous)
            except BaseException as rollback_error:
                retain_backup = True
                print(f"Rollback needs attention; backup retained at {backup}: {rollback_error}", file=sys.stderr)
            raise
    finally:
        if not retain_backup:
            shutil.rmtree(backup)


def remote_main(archive, expected_digest):
    import fcntl  # Linux only; importing the local helper works on Windows.
    if os.geteuid() != 0:
        raise ValueError("Deploy via SSH as root")
    archive = Path(archive)
    try:
        if digest(archive) != expected_digest:
            raise ValueError("Uploaded archive checksum mismatch")
        with tempfile.TemporaryDirectory(prefix="client-deploy-") as temp:
            stage = Path(temp)
            unpack(archive, stage)
            manifest = json.loads((stage / "DEPLOYMENT.json").read_text())
            settings = manifest["project"]
            validate_settings(settings)
            actual = source_files(stage)
            actual.pop("DEPLOYMENT.json")
            if manifest["schema"] != 1 or actual != manifest["files"]:
                raise ValueError("Release file manifest mismatch")
            if validate_source(stage, settings) != manifest["version"]:
                raise ValueError("Release version mismatch")
            with open(f"/run/lock/{settings['name']}-deploy.lock", "w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise RuntimeError("Another deployment of this project is running") from None
                install_release(stage, manifest)
            print(f"Verified {settings['name']} {manifest['version']} ({manifest['commit'][:12]}).")
    finally:
        archive.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Deploy a verified source snapshot over SSH (Python 3.11+).")
    parser.add_argument("target", nargs="?", help="SSH destination, for example root@example-host")
    parser.add_argument("--working-tree", action="store_true", help="include tracked edits, staged additions and deletions")
    parser.add_argument("--design-only", action="store_true", help="explicitly start design mode; resume an active runtime")
    frontend = parser.add_mutually_exclusive_group()
    frontend.add_argument("--frontend", help="frontend directory (default: frontend/ for projects with a frontend)")
    frontend.add_argument("--keep-frontend", action="store_true", help="explicitly retain the currently installed frontend")
    parser.add_argument("--output", type=Path, help="build an archive locally without connecting or deploying")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    settings = json.loads((root / "deploy/project.json").read_text(encoding="utf-8"))
    validate_settings(settings)
    if not args.output and (not args.target or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", args.target)):
        parser.error("provide an SSH target or --output ARCHIVE")
    if args.design_only and not settings.get("design_service"):
        parser.error("this project has no design service")
    if (args.frontend or args.keep_frontend) and not settings.get("frontend"):
        parser.error("this project has no frontend bundle")
    commands = ("git",) if args.output else ("git", "ssh", "scp")
    for command in commands:
        if not shutil.which(command):
            parser.error(f"required command is missing: {command}")
    with tempfile.TemporaryDirectory(prefix="client-deploy-") as temp:
        archive, manifest = build_bundle(root, settings, Path(temp), working_tree=args.working_tree,
                                        frontend=args.frontend, keep_frontend=args.keep_frontend,
                                        design_only=args.design_only)
        print(f"Prepared {settings['name']} {manifest['version']} from {manifest['source']} "
              f"({manifest['commit'][:12]}); frontend: {manifest['frontend']}.", flush=True)
        if args.output:
            if args.output.exists():
                raise ValueError(f"Output already exists: {args.output}")
            shutil.copyfile(archive, args.output)
            print(f"Archive: {args.output}; SHA256: {digest(archive)}")
            return 0
        remote_archive = f"/tmp/{settings['name']}-deploy-{uuid.uuid4().hex}.tar.gz"
        try:
            run("scp", "--", str(archive), f"{args.target}:{remote_archive}")
            command = shlex.join(["python3", "-", "--remote", remote_archive, digest(archive)])
            run("ssh", args.target, command, input=Path(__file__).read_bytes())
        finally:
            # Covers interrupted SCP and remote preflight failures. The remote
            # helper owns its extracted staging directory and rollback lifecycle.
            subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                            args.target, shlex.join(["rm", "-f", "--", remote_archive])],
                           check=False, timeout=15)
    print("Deployment complete.")
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--remote":
            def interrupted(signum, frame):
                raise RuntimeError(f"Deployment interrupted by signal {signum}")
            for sig in (signal.SIGHUP, signal.SIGTERM):
                signal.signal(sig, interrupted)
            remote_main(*sys.argv[2:])
        else:
            raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
