"""Safety tests for the shared deployment engine."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "deploy.py"
spec = importlib.util.spec_from_file_location("shared_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = deploy
spec.loader.exec_module(deploy)


class ArchiveSafetyTests(unittest.TestCase):
    def test_unpack_refuses_escaping_paths_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, configure in (
                ("escape.tar", lambda item: None),
                ("link.tar", lambda item: setattr(item, "type", tarfile.SYMTYPE)),
            ):
                archive = root / name
                with tarfile.open(archive, "w") as output:
                    item = tarfile.TarInfo("../outside" if name == "escape.tar" else "link")
                    item.size = 0
                    configure(item)
                    output.addfile(item)
                with self.subTest(name=name), self.assertRaises(ValueError):
                    deploy.unpack(archive, root / "output")

    def test_working_tree_is_complete_but_excludes_untracked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "demo").mkdir()
            (root / "deploy").mkdir()
            (root / "examples").mkdir()
            files = {
                "demo/__init__.py": '__version__ = "1.0.0"\n',
                "demo/removed.py": "old = True\n",
                "deploy/install.sh": "#!/bin/sh\nset -eu\n",
                "deploy/demo.service": "[Service]\nExecStart=/bin/true\n",
                "deploy/demo.env.example": "DEMO=\n",
                "examples/config.toml": "enabled = true\n",
                "pyproject.toml": '[project]\nname = "demo"\nversion = "1.0.0"\n',
            }
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            environment = dict(
                **os.environ,
                GIT_AUTHOR_NAME="Example", GIT_AUTHOR_EMAIL="example@example.com",
                GIT_COMMITTER_NAME="Example", GIT_COMMITTER_EMAIL="example@example.com",
            )
            subprocess.run(["git", "commit", "--quiet", "-m", "initial"],
                           cwd=root, check=True, env=environment)

            (root / "demo/__init__.py").write_text('__version__ = "1.0.0"\nchanged = True\n')
            (root / "demo/removed.py").unlink()
            (root / "demo/staged.py").write_text("staged = True\n")
            subprocess.run(["git", "add", "demo/staged.py"], cwd=root, check=True)
            (root / "demo/untracked.py").write_text("untracked = True\n")

            settings = {
                "name": "demo", "package": "demo", "services": ["demo"],
                "primary": "demo", "config": "/etc/demo/config.toml", "health": {},
            }
            with tempfile.TemporaryDirectory() as work_name:
                work = Path(work_name)
                archive, manifest = deploy.build_bundle(
                    root, settings, work, working_tree=True)
                extracted = work / "extracted"
                extracted.mkdir()
                deploy.unpack(archive, extracted)
                self.assertTrue((extracted / "demo/staged.py").is_file())
                self.assertIn("changed = True", (extracted / "demo/__init__.py").read_text())
                self.assertFalse((extracted / "demo/removed.py").exists())
                self.assertFalse((extracted / "demo/untracked.py").exists())
                actual = deploy.source_files(extracted)
                actual.pop("DEPLOYMENT.json")
                self.assertEqual(manifest["files"], actual)
                (extracted / "demo/staged.py").write_text("tampered = True\n")
                tampered = deploy.source_files(extracted)
                tampered.pop("DEPLOYMENT.json")
                self.assertNotEqual(manifest["files"], tampered)

    def test_one_ssh_stream_carries_helper_and_archive_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "release.tar.gz"
            archive.write_bytes(b"verified release bytes")
            helper = (
                b"from pathlib import Path\n"
                b"import sys\n"
                b"Path(sys.argv[2] + '.seen').write_bytes("
                b"Path(sys.argv[2]).read_bytes())\n"
            )
            payload = io.BytesIO()
            deploy.write_deploy_payload(payload, helper, archive)
            remote = root / "remote-release.tar.gz"
            subprocess.run(
                [sys.executable, "-c", deploy.REMOTE_BOOTSTRAP,
                 str(remote), "unused-in-test"],
                input=payload.getvalue(), check=True)
            self.assertEqual(
                Path(str(remote) + ".seen").read_bytes(), archive.read_bytes())
            self.assertFalse(remote.exists())


class RollbackTests(unittest.TestCase):
    def test_restore_reinstates_old_paths_and_removes_new_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "package"
            package.mkdir()
            (package / "old.py").write_text("old\n")
            unit = root / "service.unit"
            unit.write_text("old unit\n")
            newly_created = root / "new-metadata.json"
            backup = root / "backup"
            backup.mkdir()

            records = deploy.save_paths([package, unit, newly_created], backup)
            shutil.rmtree(package)
            package.mkdir()
            (package / "new.py").write_text("new\n")
            unit.write_text("new unit\n")
            newly_created.write_text(json.dumps({"new": True}))

            deploy.restore_paths(records)
            self.assertEqual((package / "old.py").read_text(), "old\n")
            self.assertFalse((package / "new.py").exists())
            self.assertEqual(unit.read_text(), "old unit\n")
            self.assertFalse(newly_created.exists())


class ServiceStateTests(unittest.TestCase):
    def test_normal_deploy_preserves_inactive_optional_service(self):
        settings = {
            "services": ["runtime", "design"], "primary": "runtime",
            "design_service": "design",
        }
        plan = deploy.service_plan(
            settings, {"design_only": False}, {"runtime": True, "design": False})
        self.assertEqual(plan, ["runtime"])

    def test_active_services_resume_and_design_only_explicitly_starts_design(self):
        settings = {
            "services": ["runtime", "design"], "primary": "runtime",
            "design_service": "design",
        }
        self.assertEqual(
            deploy.service_plan(
                settings, {"design_only": False}, {"runtime": True, "design": True}),
            ["runtime", "design"])
        self.assertEqual(
            deploy.service_plan(
                settings, {"design_only": True}, {"runtime": True, "design": False}),
            ["design", "runtime"])


if __name__ == "__main__":
    unittest.main()
