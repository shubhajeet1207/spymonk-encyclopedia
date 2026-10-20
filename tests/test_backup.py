"""The archive survives a host that wipes its disk: restore before serving, mirror after."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.core import config, reindex
from backend.services import backup
from backend.services.backup import BackupError, BackupManager, DirectoryTarget, HfDatasetTarget

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class BackupManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.remote = Path(temporary.name) / "remote"
        self.local = Path(temporary.name) / "local"
        _write(self.remote / "memory/topics/a.json", '{"topic": "A"}')
        _write(self.remote / "data/pdfs/paper.pdf", "%PDF-1.4 remote")
        _write(self.remote / "chroma_db/chroma.sqlite3", "index files are rebuilt, not restored")
        _write(self.remote / "data/web_html/.partial.tmp", "temporary")
        self.manager = BackupManager(DirectoryTarget(self.remote), self.local, interval=60)

    def test_restore_brings_back_knowledge_but_not_the_index_or_temporary_files(self) -> None:
        restored = self.manager.restore()

        self.assertEqual(restored, 2)
        self.assertEqual((self.local / "memory/topics/a.json").read_text(encoding="utf-8"), '{"topic": "A"}')
        self.assertFalse((self.local / "chroma_db").exists())
        self.assertFalse((self.local / "data/web_html/.partial.tmp").exists())
        self.assertEqual(self.manager.pending(), [])

    def test_restore_never_overwrites_a_local_file(self) -> None:
        _write(self.local / "memory/topics/a.json", '{"topic": "A", "newer": true}')
        self.manager.restore()
        self.assertIn("newer", (self.local / "memory/topics/a.json").read_text(encoding="utf-8"))

    def test_sync_uploads_only_changed_content_and_never_deletes(self) -> None:
        self.manager.restore()
        new = _write(self.local / "memory/unanswered/20261020000000aaaaaaaaaaaaaaaa.json", "{}")
        self.assertEqual(self.manager.sync(), 1)
        self.assertTrue((self.remote / "memory/unanswered/20261020000000aaaaaaaaaaaaaaaa.json").exists())

        # Same bytes rewritten (an atomic save of an unchanged audit): no upload.
        topic = self.local / "memory/topics/a.json"
        topic.write_text('{"topic": "A"}', encoding="utf-8")
        os.utime(topic, ns=(topic.stat().st_atime_ns, topic.stat().st_mtime_ns + 5_000_000_000))
        self.assertEqual(self.manager.sync(), 0)

        new.unlink()
        self.assertEqual(self.manager.sync(), 0)
        self.assertTrue((self.remote / "memory/unanswered/20261020000000aaaaaaaaaaaaaaaa.json").exists())

    def test_a_failed_upload_is_retried_on_the_next_sync(self) -> None:
        self.manager.restore()
        _write(self.local / "memory/topics/b.json", '{"topic": "B"}')
        with patch.object(DirectoryTarget, "upload", side_effect=OSError("network down")):
            self.assertEqual(self.manager.sync(), 0)
        self.assertEqual(self.manager.sync(), 1)

    def test_stop_pushes_what_is_left(self) -> None:
        self.manager.restore()
        self.manager.start()
        _write(self.local / "logs/research/20261020T000000Z-saved-x-abc123.json", "{}")
        self.manager.stop()
        self.assertTrue((self.remote / "logs/research/20261020T000000Z-saved-x-abc123.json").exists())

    def test_an_unreachable_backup_stops_startup(self) -> None:
        target = MagicMock(description="test target")
        target.download.side_effect = ConnectionError("offline")
        manager = BackupManager(target, self.local, interval=60)
        with patch.object(backup.time, "sleep"), self.assertRaises(BackupError):
            manager.restore()
        self.assertEqual(target.download.call_count, 3)


class HfDatasetTargetTests(unittest.TestCase):
    def _target(self, api: MagicMock) -> HfDatasetTarget:
        target = HfDatasetTarget("me/spymonk-data", "token")
        target._api = lambda: api  # type: ignore[method-assign]
        return target

    def test_a_missing_dataset_is_created_private(self) -> None:
        from huggingface_hub.errors import RepositoryNotFoundError

        api = MagicMock()
        api.repo_info.side_effect = [RepositoryNotFoundError("missing", response=MagicMock()), MagicMock(private=True)]
        self._target(api).prepare()
        api.create_repo.assert_called_once_with("me/spymonk-data", repo_type="dataset", private=True, exist_ok=True)

    def test_a_public_dataset_is_refused(self) -> None:
        api = MagicMock()
        api.repo_info.return_value = MagicMock(private=False)
        with self.assertRaises(BackupError):
            self._target(api).prepare()


class HostedStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = self.root / "data-dir"
        self.remote = self.root / "remote"
        self.patches = [
            patch.object(config, name, value)
            for name, value in (
                ("DATA_DIR", self.data),
                ("MEMORY_DIR", self.data / "memory" / "topics"),
                ("PDF_DIR", self.data / "data" / "pdfs"),
                ("CHROMA_DIR", self.data / "chroma_db"),
                ("LOG_DIR", self.data / "logs"),
                ("INSTANCE_LOCK", False),
                ("LOG_TO_FILE", False),
                ("HOSTED", True),
                ("APP_API_TOKEN", "test-token"),
                ("BACKUP_TARGET", "directory"),
                ("BACKUP_DIRECTORY", str(self.remote)),
                ("BACKUP_INTERVAL_SECONDS", 60.0),
                ("REBUILD_INDEX_ON_START", True),
                ("EMBEDDINGS_WARMUP", False),
            )
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_hosted_startup_without_a_token_is_refused(self) -> None:
        from backend.main import create_app

        with patch.object(config, "APP_API_TOKEN", ""), self.assertRaises(RuntimeError):
            with TestClient(create_app()):
                pass

    def test_startup_restores_then_rebuilds_and_shutdown_backs_up(self) -> None:
        from backend.main import create_app

        _write(self.remote / "memory/topics/t.json", json.dumps({"topic": "T", "claims": []}))
        seen_at_rebuild: list[bool] = []

        def rebuild() -> None:
            seen_at_rebuild.append((self.data / "memory/topics/t.json").exists())

        with patch.object(reindex, "rebuild_if_missing", side_effect=rebuild):
            with TestClient(create_app(), base_url="http://localhost") as client:
                self.assertEqual(client.get("/api/health").status_code, 200)
                _write(self.data / "memory/unanswered/20261020000000bbbbbbbbbbbbbbbb.json", "{}")

        self.assertEqual(seen_at_rebuild, [True])
        self.assertTrue((self.remote / "memory/unanswered/20261020000000bbbbbbbbbbbbbbbb.json").exists())

    def test_hosted_api_hides_the_schema(self) -> None:
        from backend.main import create_app

        with patch.object(config, "API_DOCS", False):
            client = TestClient(create_app(), base_url="http://localhost")
            response = client.get("/openapi.json")
        self.assertFalse(response.headers.get("content-type", "").startswith("application/json"))


class SpaceDefaultsTests(unittest.TestCase):
    def test_a_space_gets_its_host_origin_and_frame_policy(self) -> None:
        environment = {**os.environ, "SPACE_ID": "me/spymonk", "SPACE_HOST": "me-spymonk.hf.space"}
        for name in ("APP_ALLOWED_HOSTS", "APP_ALLOWED_ORIGINS", "APP_FRAME_ANCESTORS", "SPYMONK_HOSTED", "APP_API_DOCS"):
            environment.pop(name, None)
        code = (
            "from backend.core import config; import backend.main as m; "
            "print(config.HOSTED, config.ALLOWED_HOSTS[0], config.ALLOWED_ORIGINS, config.API_DOCS); "
            "print(m.FRONTEND_CSP.decode())"
        )
        output = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=environment, capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("True me-spymonk.hf.space ('https://me-spymonk.hf.space',) False", output)
        self.assertIn("frame-ancestors https://huggingface.co", output)


class DeployScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(ROOT / "scripts"))
        import deploy_hf_space

        self.deploy = deploy_hf_space

    def test_only_code_is_deployed(self) -> None:
        files = self.deploy.deploy_files()
        self.assertIn("Dockerfile", files)
        self.assertIn("backend/main.py", files)
        self.assertEqual(files["README.md"], ROOT / "deploy" / "huggingface" / "README.md")
        forbidden = [name for name in files if name.startswith((".env", "data/", "memory/", "logs/", "chroma_db/", "tests/"))]
        self.assertEqual(forbidden, [])

    def test_a_secret_file_in_the_allow_list_stops_the_deploy(self) -> None:
        with patch.object(self.deploy, "INCLUDE", (*self.deploy.INCLUDE, ".env")), self.assertRaises(SystemExit):
            self.deploy.deploy_files()

    def test_settings_come_from_the_environment_or_fail(self) -> None:
        with patch.dict(os.environ, {"LLM_MODEL": "some/model"}):
            self.assertEqual(
                self.deploy.resolve_settings(["LLM_MODEL", "BACKUP_TARGET=hf-dataset"], {}),
                {"LLM_MODEL": "some/model", "BACKUP_TARGET": "hf-dataset"},
            )
        with self.assertRaises(SystemExit):
            self.deploy.resolve_settings(["DEFINITELY_UNSET_SETTING_NAME"], {})

    def test_space_urls_follow_hugging_face_naming(self) -> None:
        self.assertEqual(self.deploy.space_url("Me_Name/Spy.Monk"), "https://me-name-spy-monk.hf.space")


if __name__ == "__main__":
    unittest.main()
