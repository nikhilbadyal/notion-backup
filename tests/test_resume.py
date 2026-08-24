"""Tests for resume-session preservation and recovery-queue behavior."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from src.config import Settings
from src.core.backup import BackupManager
from src.core.client import ExportFailure, ExportResult, NotionClient
from src.utils.helpers import _session_file, load_session, save_session
from src.utils.redis_client import RedisClient

SPACE_ID = "test-space-00000000-0000-0000-0000-000000000000"


def make_settings(**overrides: Any) -> Settings:
    """Build test settings with minimal required Notion fields."""
    defaults: dict[str, Any] = {
        "notion_space_id": SPACE_ID,
        "notion_token_v2": "test-token",
        "notion_file_token": "test-file-token",
        "retry_delay": 0,
        "max_retries": 2,
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def _cleanup_session() -> Any:
    """Ensure the session file is removed before and after every test."""
    session_path = _session_file(SPACE_ID)
    session_path.unlink(missing_ok=True)
    yield
    session_path.unlink(missing_ok=True)


async def _noop(*_args: Any, **_kwargs: Any) -> None:
    """Async no-op for patching out network-touching methods."""
    return


# ---------------------------------------------------------------------------
# Resume session preservation
# ---------------------------------------------------------------------------


class TestResumeSessionPreservation:
    """A transient export failure must keep the session so it can be resumed."""

    def test_transient_failure_preserves_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URL_NOT_READY (file not ready) must not delete the session file."""
        save_session(SPACE_ID, "task-abc", 1700000000000)
        manager = BackupManager(make_settings())

        mock_export = AsyncMock(return_value=ExportResult(file=None, failure=ExportFailure.URL_NOT_READY))
        monkeypatch.setattr(manager.notion_client, "export_workspace", mock_export)
        monkeypatch.setattr(manager, "_test_connections", _noop)

        result = asyncio.run(manager.run_backup(resume=True))

        assert result is False
        session = load_session(SPACE_ID)
        assert session is not None
        assert session["task_id"] == "task-abc"

    def test_permanent_failure_clears_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TASK_FAILED means the task is dead; the session must be cleared."""
        save_session(SPACE_ID, "task-abc", 1700000000000)
        manager = BackupManager(make_settings())

        mock_export = AsyncMock(return_value=ExportResult(file=None, failure=ExportFailure.TASK_FAILED))
        monkeypatch.setattr(manager.notion_client, "export_workspace", mock_export)
        monkeypatch.setattr(manager, "_test_connections", _noop)

        result = asyncio.run(manager.run_backup(resume=True))

        assert result is False
        assert load_session(SPACE_ID) is None


# ---------------------------------------------------------------------------
# Recovered resumed task skips main export
# ---------------------------------------------------------------------------


class TestRecoveredResumedTask:
    """If the recovery queue already recovered the resumed task, skip the export."""

    def test_recovered_task_skips_export_and_clears_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_backup must return True, clear the session, and notify."""
        save_session(SPACE_ID, "task-abc", 1700000000000)
        manager = BackupManager(make_settings(redis_host="localhost"))

        mock_export = AsyncMock()

        def fake_pending() -> list[dict[str, Any]]:
            return [{"task_id": "task-abc", "enqueued_at": 1700000000000, "retry_count": 0}]

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        async def fake_download(_download_url: str, temp_dir: Path) -> Path:
            backup_file = Path(temp_dir) / "notion-export-markdown_2026-01-01_00-00-00.zip"
            backup_file.write_bytes(b"dummy")
            return backup_file

        async def fake_storage(_backup_file: Path) -> str:
            return "local://backup.zip"

        sent: list[tuple[Any, ...]] = []

        async def fake_send(
            backup_filename: str,
            file_size: int,
            storage_location: str,
            dry_run: bool = False,
        ) -> None:
            sent.append((backup_filename, file_size, storage_location, dry_run))

        monkeypatch.setattr(manager.notion_client, "export_workspace", mock_export)
        monkeypatch.setattr(manager, "_test_connections", _noop)
        monkeypatch.setattr(manager.redis_client, "get_pending_exports", fake_pending)
        monkeypatch.setattr(manager.notion_client, "get_notifications", fake_notifications)
        monkeypatch.setattr(
            manager.notion_client,
            "extract_download_url_from_notifications",
            MagicMock(return_value="https://example.com/export.zip"),
        )
        monkeypatch.setattr(manager.notion_client, "_download_file", fake_download)
        monkeypatch.setattr(manager, "_handle_storage", fake_storage)
        monkeypatch.setattr(manager, "_handle_notification_marking", _noop)
        monkeypatch.setattr(manager, "_handle_notification_archiving", _noop)
        monkeypatch.setattr(manager, "_send_success_notification", fake_send)

        result = asyncio.run(manager.run_backup(resume=True))

        assert result is True
        mock_export.assert_not_awaited()
        assert load_session(SPACE_ID) is None
        assert len(sent) == 1
        filename, file_size, storage_location, dry_run = sent[0]
        assert filename == "notion-export-markdown_2026-01-01_00-00-00.zip"
        assert file_size == 5
        assert storage_location == "local://backup.zip"
        assert dry_run is False


# ---------------------------------------------------------------------------
# Recovery queue deduplication
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal in-memory stand-in for the redis client."""

    def __init__(self) -> None:
        self.queue: list[str] = []

    def ping(self) -> bool:
        return True

    def lrange(self, _key: str, _start: int, _end: int) -> list[str]:
        return self.queue

    def rpush(self, _key: str, *values: str) -> int:
        self.queue.extend(values)
        return len(self.queue)


class TestRecoveryQueueDedupe:
    """Pushing the same task twice must not create duplicate queue entries."""

    def test_duplicate_task_id_is_skipped(self) -> None:
        client = RedisClient(make_settings())
        client.client = FakeRedis()

        client.push_pending_export("task-1", 1000)
        client.push_pending_export("task-1", 2000)
        client.push_pending_export("task-2", 3000)

        assert len(client.client.queue) == 2


# ---------------------------------------------------------------------------
# _wait_for_download_url
# ---------------------------------------------------------------------------


class TestWaitForDownloadUrl:
    """_wait_for_download_url must poll until the URL is ready or the window elapses."""

    def test_url_found_on_first_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A URL available immediately should be returned without sleeping."""
        client = NotionClient(make_settings(max_export_wait_time=60, export_poll_interval=10))

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        monkeypatch.setattr(client, "get_notifications", fake_notifications)
        monkeypatch.setattr(
            client,
            "extract_download_url_from_notifications",
            MagicMock(return_value="https://example.com/export.zip"),
        )

        url = asyncio.run(client._wait_for_download_url("task-1", 1000))  # noqa: SLF001

        assert url == "https://example.com/export.zip"

    def test_url_found_after_several_polls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The URL should be returned once it appears, even if not on the first poll."""
        client = NotionClient(make_settings(max_export_wait_time=60, export_poll_interval=0))

        calls = 0

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        def fake_extract(_notifications: dict[str, Any], started_after_ms: int = 0) -> str | None:
            nonlocal calls
            calls += 1
            # Return the URL on the third poll
            return "https://example.com/export.zip" if calls >= 3 else None

        monkeypatch.setattr(client, "get_notifications", fake_notifications)
        monkeypatch.setattr(client, "extract_download_url_from_notifications", fake_extract)

        url = asyncio.run(client._wait_for_download_url("task-1", 1000))  # noqa: SLF001

        assert url == "https://example.com/export.zip"
        assert calls == 3

    def test_times_out_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the URL never appears, return None after the wait window elapses."""
        client = NotionClient(make_settings(max_export_wait_time=0, export_poll_interval=10))

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        monkeypatch.setattr(client, "get_notifications", fake_notifications)
        monkeypatch.setattr(
            client,
            "extract_download_url_from_notifications",
            MagicMock(return_value=None),
        )

        url = asyncio.run(client._wait_for_download_url("task-1", 1000))  # noqa: SLF001

        assert url is None


# ---------------------------------------------------------------------------
# export_workspace failure mapping
# ---------------------------------------------------------------------------


class TestExportWorkspaceFailureMapping:
    """export_workspace must report the right failure reason."""

    def test_trigger_failure(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = NotionClient(make_settings())

        async def fake_trigger() -> None:
            return None

        monkeypatch.setattr(client, "_trigger_export_task", fake_trigger)

        result = asyncio.run(client.export_workspace(tmp_path))

        assert result.file is None
        assert result.failure == ExportFailure.TRIGGER_FAILED

    def test_task_failure(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = NotionClient(make_settings())

        async def fake_trigger() -> str:
            return "task-1"

        async def fake_poll(_task_id: str) -> bool:
            return False

        monkeypatch.setattr(client, "_trigger_export_task", fake_trigger)
        monkeypatch.setattr(client, "_poll_task_completion", fake_poll)

        result = asyncio.run(client.export_workspace(tmp_path))

        assert result.file is None
        assert result.failure == ExportFailure.TASK_FAILED

    def test_url_not_ready(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = NotionClient(
            make_settings(max_export_wait_time=0, export_poll_interval=10),
        )

        async def fake_trigger() -> str:
            return "task-1"

        async def fake_poll(_task_id: str) -> bool:
            return True

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        monkeypatch.setattr(client, "_trigger_export_task", fake_trigger)
        monkeypatch.setattr(client, "_poll_task_completion", fake_poll)
        monkeypatch.setattr(client, "get_notifications", fake_notifications)

        result = asyncio.run(client.export_workspace(tmp_path))

        assert result.file is None
        assert result.failure == ExportFailure.URL_NOT_READY

    def test_download_failure(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = NotionClient(make_settings())

        async def fake_trigger() -> str:
            return "task-1"

        async def fake_poll(_task_id: str) -> bool:
            return True

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        async def fake_download(_download_url: str, _temp_dir: Path) -> None:
            return None

        monkeypatch.setattr(client, "_trigger_export_task", fake_trigger)
        monkeypatch.setattr(client, "_poll_task_completion", fake_poll)
        monkeypatch.setattr(client, "get_notifications", fake_notifications)
        monkeypatch.setattr(
            client,
            "extract_download_url_from_notifications",
            MagicMock(return_value="https://example.com/export.zip"),
        )
        monkeypatch.setattr(client, "_download_file", fake_download)

        result = asyncio.run(client.export_workspace(tmp_path))

        assert result.file is None
        assert result.failure == ExportFailure.DOWNLOAD_FAILED

    def test_success(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = NotionClient(make_settings())

        async def fake_trigger() -> str:
            return "task-1"

        async def fake_poll(_task_id: str) -> bool:
            return True

        async def fake_notifications() -> dict[str, Any]:
            return {"recordMap": {"activity": {}, "notification": {}}}

        async def fake_download(_download_url: str, temp_dir: Path) -> Path:
            backup_file = Path(temp_dir) / "notion-export-markdown_2026-01-01_00-00-00.zip"
            backup_file.write_bytes(b"dummy")
            return backup_file

        monkeypatch.setattr(client, "_trigger_export_task", fake_trigger)
        monkeypatch.setattr(client, "_poll_task_completion", fake_poll)
        monkeypatch.setattr(client, "get_notifications", fake_notifications)
        monkeypatch.setattr(
            client,
            "extract_download_url_from_notifications",
            MagicMock(return_value="https://example.com/export.zip"),
        )
        monkeypatch.setattr(client, "_download_file", fake_download)

        result = asyncio.run(client.export_workspace(tmp_path))

        assert result.file is not None
        assert result.failure is None
        # A fresh export saves the session for later resumption
        session = load_session(SPACE_ID)
        assert session is not None
        assert session["task_id"] == "task-1"


# ---------------------------------------------------------------------------
# Download 403 handling
# ---------------------------------------------------------------------------


class TestDownloadFile403:
    """A 403 from file.notion.com must fail fast with both cookies sent."""

    def test_session_sends_both_cookies(self) -> None:
        """The session Cookie header must carry token_v2 and file_token."""
        client = NotionClient(make_settings())
        cookie_header = client.session.headers["Cookie"]
        assert f"token_v2={client.settings.notion_token_v2.get_secret_value()}" in cookie_header
        assert f"file_token={client.settings.notion_file_token.get_secret_value()}" in cookie_header

    def test_403_returns_none_after_fallback_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """403 must log the body, try the no-cookie fallback, and return None if both fail."""
        client = NotionClient(make_settings())
        session = MagicMock()
        response = MagicMock()
        response.status_code = 403
        response.text = '{"error":"forbidden"}'
        response.raise_for_status.side_effect = requests.HTTPError("403 Client Error", response=response)
        session.get.return_value = response
        client.session = session

        def fake_fallback(_download_url: str, _file_path: Path) -> None:
            return None

        monkeypatch.setattr(client, "_download_without_cookies", fake_fallback)

        result = asyncio.run(client._download_file("https://file.notion.com/export.zip", tmp_path))  # noqa: SLF001

        assert result is None
        # 403 is not retried - exactly one attempt with cookies
        assert session.get.call_count == 1

    def test_403_fallback_succeeds_without_cookies(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the no-cookie fallback succeeds, the file is returned."""
        client = NotionClient(make_settings())
        session = MagicMock()
        response = MagicMock()
        response.status_code = 403
        response.text = '{"error":"forbidden"}'
        response.raise_for_status.side_effect = requests.HTTPError("403 Client Error", response=response)
        session.get.return_value = response
        client.session = session

        def fake_fallback(_download_url: str, file_path: Path) -> Path:
            backup_file = Path(file_path)
            backup_file.write_bytes(b"dummy")
            return backup_file

        monkeypatch.setattr(client, "_download_without_cookies", fake_fallback)

        result = asyncio.run(client._download_file("https://file.notion.com/export.zip", tmp_path))  # noqa: SLF001

        assert result is not None
        assert result.read_bytes() == b"dummy"

    def test_file_token_probe_403_is_non_fatal(self) -> None:
        """A 403 probe response must warn but still report success."""
        client = NotionClient(make_settings())
        session = MagicMock()
        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"space": {SPACE_ID: {}}}
        session.post.return_value = post_response
        probe_response = MagicMock()
        probe_response.status_code = 403
        session.get.return_value = probe_response
        client.session = session

        result = asyncio.run(client.test_connection())

        assert result.success is True
        # The probe must have been sent (the session carries the file_token cookie)
        session.get.assert_called_once()
