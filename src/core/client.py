"""Notion API client for exporting workspaces."""

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import requests

from src.config import Settings
from src.utils import get_timestamp_string, retry_async, save_session
from src.utils.redis_client import RedisClient

logger = logging.getLogger(__name__)


@dataclass
class ConnectionResult:
    """Result of a Notion credential/connection check."""

    success: bool
    message: str


class ExportFailure(StrEnum):
    """Reason an export failed, used to decide whether a session can be resumed."""

    TASK_FAILED = "task_failed"  # Permanent: Notion reported the task failed
    TRIGGER_FAILED = "trigger_failed"  # Transient: could not enqueue the export task
    URL_NOT_READY = "url_not_ready"  # Transient: task succeeded but download URL not available yet
    DOWNLOAD_FAILED = "download_failed"  # Transient: download failed after retries


@dataclass
class ExportResult:
    """Result of an export attempt."""

    file: Path | None
    failure: ExportFailure | None = None


# noinspection PyBroadException
class NotionClient:
    """Client for interacting with Notion's export API."""

    BASE_URL = "https://www.notion.so/api"
    API_VERSION = "v3"
    ENQUEUE_ENDPOINT = f"{BASE_URL}/{API_VERSION}/enqueueTask"
    GET_TASKS_ENDPOINT = f"{BASE_URL}/{API_VERSION}/getTasks"
    GET_SPACES_ENDPOINT = f"{BASE_URL}/{API_VERSION}/getSpaces"
    NOTIFICATION_ENDPOINT = f"{BASE_URL}/{API_VERSION}/getNotificationLogV2"
    MARK_READ_ENDPOINT = f"{BASE_URL}/{API_VERSION}/saveTransactionsMain"
    CONTENT_TYPE = "application/json"

    TOKEN_V2 = "token_v2"  # noqa: S105
    FILE_TOKEN = "file_token"  # noqa: S105

    def __init__(self, settings: Settings) -> None:
        """Initialize the Notion client."""
        self.settings = settings
        self.session = requests.Session()
        self.export_notification_id: str | None = None  # Track notification ID for marking as read
        self.redis_client = RedisClient(settings)

        # Set up session with default headers. The Cookie header carries BOTH
        # token_v2 and file_token (browser parity): www.notion.so API calls
        # need token_v2, and file.notion.com downloads need file_token.
        # Note: requests drops a per-request cookies= param when a Cookie
        # header is already set on the session, so both cookies must live here.
        token_v2 = self.settings.notion_token_v2.get_secret_value()
        file_token = self.settings.notion_file_token.get_secret_value()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0",
                "x-notion-space-id": self.settings.notion_space_id,
                "Cookie": f"{self.TOKEN_V2}={token_v2}; {self.FILE_TOKEN}={file_token}",
                "Content-Type": self.CONTENT_TYPE,
            },
        )
        logger.debug(
            "Session cookies set: %s=%s...%s, %s=%s...%s",
            self.TOKEN_V2,
            token_v2[:4],
            token_v2[-4:],
            self.FILE_TOKEN,
            file_token[:4],
            file_token[-4:],
        )

        logger.info("Notion client initialized")

    async def test_connection(self) -> ConnectionResult:
        """Verify Notion credentials without triggering an export.

        Calls the lightweight ``getSpaces`` endpoint to confirm the
        ``token_v2`` cookie and ``space_id`` are valid. This runs before any
        export/recovery work so invalid credentials fail fast.

        Returns
        -------
            ConnectionResult indicating whether the credentials are valid.
        """
        try:
            response = self.session.post(
                self.GET_SPACES_ENDPOINT,
                json={},
                timeout=30,
            )

            if response.status_code == 401:
                msg = "Notion token invalid or expired (HTTP 401)"
                logger.error(msg)
                return ConnectionResult(success=False, message=msg)

            if response.status_code != 200:
                msg = f"Notion API returned HTTP {response.status_code}"
                logger.error(msg)
                return ConnectionResult(success=False, message=msg)

            data = response.json()
            # getSpaces returns a dict keyed by user ID, with the space map
            # nested under each user's "space" key (older responses had
            # "space" at the top level). Handle both shapes.
            spaces: dict[str, Any] = {}
            if isinstance(data.get("space"), dict):
                spaces.update(data["space"])
            for user_payload in data.values():
                if isinstance(user_payload, dict) and isinstance(user_payload.get("space"), dict):
                    spaces.update(user_payload["space"])

            if self.settings.notion_space_id not in spaces:
                msg = f"Notion space '{self.settings.notion_space_id}' not found for this token"
                logger.error(msg)
                return ConnectionResult(success=False, message=msg)

            # Best-effort check of the file_token cookie (used for downloads).
            # file.notion.com returns 404 for a nonexistent path when the
            # cookie is valid, and 403 when it is missing/expired. This is a
            # heuristic, so a failure here is only a warning, not fatal.
            try:
                probe = self.session.get(
                    "https://file.notion.com/f/t/notion-backup-probe",
                    timeout=15,
                )
                if probe.status_code == 403:
                    logger.warning(
                        "file_token cookie appears invalid or expired (probe returned HTTP 403); "
                        "downloads will fail with 403 - refresh NOTION_FILE_TOKEN from your browser",
                    )
                else:
                    logger.debug("file_token probe returned HTTP %d", probe.status_code)
            except Exception:
                logger.debug("file_token probe failed (non-fatal)", exc_info=True)

            msg = f"Notion credentials valid for space '{self.settings.notion_space_id}'"
            logger.info(msg)
            return ConnectionResult(success=True, message=msg)

        except Exception as e:
            msg = f"Failed to verify Notion credentials: {e}"
            logger.exception(msg)
            return ConnectionResult(success=False, message=msg)

    @retry_async(max_retries=3, delay=5.0)
    async def export_workspace(
        self,
        temp_dir: Path,
        resume_task_id: str | None = None,
        resume_started_at_ms: int | None = None,
    ) -> ExportResult:
        """
        Export the Notion workspace and return the downloaded file.

        Args:
            temp_dir: Temporary directory for download
            resume_task_id: If set, skip triggering a new export and resume this task
            resume_started_at_ms: Wall-clock time (ms) the original export was started

        Returns
        -------
            ExportResult with the downloaded file path, or a failure reason.
        """
        try:
            if resume_task_id and resume_started_at_ms:
                logger.info("Resuming previous export session for task %s", resume_task_id)
                task_id = resume_task_id
                export_started_at_ms = resume_started_at_ms
            else:
                # Record wall-clock time (ms) before triggering export so we can
                # filter out stale notifications that predate this export run.
                export_started_at_ms = int(time.time() * 1000)

                # Phase 1: Trigger export task
                task_id = await self._trigger_export_task()
                if not task_id:
                    logger.error("Failed to trigger export task")
                    return ExportResult(file=None, failure=ExportFailure.TRIGGER_FAILED)

                logger.debug("Export task triggered successfully with task ID: %s", task_id)
                save_session(self.settings.notion_space_id, task_id, export_started_at_ms)

            # Phase 2: Poll for task completion (returns True on success)
            task_succeeded = await self._poll_task_completion(task_id)
            if not task_succeeded:
                logger.error("Failed to get task completion status")
                return ExportResult(file=None, failure=ExportFailure.TASK_FAILED)

            logger.info("Export task completed successfully")

            # Phase 3: Fetch notifications and extract download URL.
            # Notion no longer returns the download URL in the getTasks response.
            # Instead we poll getNotificationLogV2 for an "export-completed"
            # activity whose timestamp is >= our export_started_at_ms.
            download_url = await self._wait_for_download_url(task_id, export_started_at_ms)

            if not download_url:
                logger.error("Failed to extract download URL")
                if self.settings.redis_host:
                    # Store task_id for recovery; timestamp is our wall-clock
                    # start time (best-effort, used to filter stale notifications).
                    self.redis_client.push_pending_export(task_id, export_started_at_ms)
                return ExportResult(file=None, failure=ExportFailure.URL_NOT_READY)
            # Phase 4: Download file
            backup_file = await self._download_file(download_url, temp_dir)
            if backup_file is None:
                return ExportResult(file=None, failure=ExportFailure.DOWNLOAD_FAILED)
            return ExportResult(file=backup_file)

        except Exception:
            logger.exception("Failed to export workspace")
            return ExportResult(file=None, failure=ExportFailure.DOWNLOAD_FAILED)

    async def _wait_for_download_url(self, task_id: str, export_started_at_ms: int) -> str | None:
        """Poll notifications until the export download URL is available.

        For large workspaces the export task can report ``success`` in
        ``getTasks`` well before the ``export-completed`` notification (which
        carries the download URL) is ready. We therefore keep polling for up
        to ``max_export_wait_time`` seconds (configurable via
        ``MAX_EXPORT_WAIT_TIME``), checking every ``export_poll_interval``
        seconds, so a large backup completes in a single run instead of
        failing with ``url_not_ready`` and needing a second run to resume.

        Args:
            task_id: The export task ID
            export_started_at_ms: Wall-clock time (ms) the export was triggered

        Returns
        -------
            The download URL, or None if it never became available within the
            configured wait window.
        """
        max_wait_time = self.settings.max_export_wait_time
        check_interval = self.settings.export_poll_interval
        elapsed_time = 0

        while elapsed_time < max_wait_time:
            notifications = await self.get_notifications()
            if notifications:
                msg = f"Received {len(notifications.get('notificationIds', []))} notifications."
                logger.debug(msg)
                # Pass the wall-clock start time so we only pick up
                # notifications created after we triggered this export.
                download_url = self.extract_download_url_from_notifications(
                    notifications,
                    started_after_ms=export_started_at_ms,
                )
                if download_url:
                    logger.info("Download URL obtained")
                    return download_url
            else:
                logger.info("No notifications received on this poll")

            await asyncio.sleep(check_interval)
            elapsed_time += check_interval
            logger.info("Waiting for download URL to become available... (%d seconds)", elapsed_time)

        logger.error(
            "Download URL did not become available within %d seconds "
            "(configurable via MAX_EXPORT_WAIT_TIME)",
            max_wait_time,
        )
        return None

    async def _trigger_export_task(self) -> str | None:
        """Trigger the export task and return the task ID."""
        task_data = {
            "task": {
                "eventName": "exportSpace",
                "request": {
                    "spaceId": self.settings.notion_space_id,
                    "exportOptions": {
                        "exportType": self.settings.export_type.value,
                        "timeZone": self.settings.time_zone,
                        "locale": "en",
                        "collectionViewExportType": "currentView",
                        "flattenExportFiletree": self.settings.flatten_export_filetree,
                    },
                    "recursive": True,
                    "shouldExportComments": self.settings.export_comments,
                },
                "cellRouting": {
                    "spaceIds": [],
                },
            },
        }

        for retry in range(self.settings.max_retries):
            try:
                response = self.session.post(
                    self.ENQUEUE_ENDPOINT,
                    json=task_data,
                    timeout=30,
                )

                if response.status_code == 429:
                    logger.error("Rate limit exceeded. Exiting.")
                    return None

                if response.status_code == 200:
                    data = response.json()
                    task_id = data.get("taskId")
                    if task_id:
                        logger.debug("Export task ID: %s", task_id)
                        return str(task_id)

                logger.warning("Retry %d: Export trigger failed (HTTP %d)", retry + 1, response.status_code)

                if retry < self.settings.max_retries - 1:
                    await asyncio.sleep(self.settings.retry_delay)

            except Exception as e:
                logger.warning("Retry %d: Export task trigger error: %s", retry + 1, e)
                if retry < self.settings.max_retries - 1:
                    await asyncio.sleep(self.settings.retry_delay)

        return None

    async def _poll_task_completion(self, task_id: str) -> bool:
        """Poll for task completion.

        Notion no longer returns an enqueuedAt timestamp in the getTasks
        response (breaking API change ~Jan 2025).  We now simply wait for
        the task state to become 'success'.

        Returns True if the task succeeded, False otherwise.
        """
        task_data = {"taskIds": [task_id]}
        max_wait_time = self.settings.max_export_wait_time
        check_interval = self.settings.export_poll_interval
        elapsed_time = 0

        while elapsed_time < max_wait_time:
            result = await self._poll_once(task_data)
            # True  -> task succeeded
            # False -> task failed (non-retryable)
            # None  -> still in progress, keep polling
            if result is True:
                return True
            if result is False:
                return False
            await asyncio.sleep(check_interval)
            elapsed_time += check_interval
            logger.info("Waiting for export task to complete... (%d seconds)", elapsed_time)

        logger.error(
            "Export task did not complete within %d seconds (configurable via MAX_EXPORT_WAIT_TIME)",
            max_wait_time,
        )
        return False

    async def _poll_once(self, task_data: dict[str, Any]) -> bool | None:
        """Poll Notion for task status once.

        Returns
        -------
            True  – task completed successfully.
            False – task failed (stop polling).
            None  – task still in progress (continue polling).
        """
        try:
            response = self.session.post(
                self.GET_TASKS_ENDPOINT,
                json=task_data,
                timeout=30,
            )

            if response.status_code == 429:
                logger.warning("Rate limit exceeded during polling.")
                # Treat rate-limit as "try again later"
                return None

            if response.status_code != 200:
                return None

            data = response.json()
            logger.debug("Task polling response: \n%s", json.dumps(data, indent=2))
            results = data.get("results", [])
            if not results:
                return None

            task_result = results[0]
            task_state = task_result.get("state")

            if task_state == "success":
                # Log pages exported if the status object is present
                pages_exported = task_result.get("status", {}).get("pagesExported")
                if pages_exported is not None:
                    logger.info("Task completed successfully. Pages exported: %s", pages_exported)
                else:
                    logger.info("Task completed successfully.")
                return True
            if task_state == "failure":
                logger.error("Export task failed.")
                return False
            # Any other state (e.g. "in_progress") means keep polling
            logger.info("Task state: %s. Continuing to poll...", task_state)

        except Exception as e:
            logger.warning("Error polling task status: %s", e)
        return None

    async def get_notifications(self) -> dict[str, Any] | None:
        """Fetch the latest notification log from Notion."""
        notification_data = {
            "spaceId": self.settings.notion_space_id,
            "size": 20,
            "type": "unread_and_read",
            "variant": "no_grouping",
        }

        try:
            response = self.session.post(
                self.NOTIFICATION_ENDPOINT,
                json=notification_data,
                timeout=30,
            )
        except Exception as e:
            logger.warning("Exception fetching notifications: %s", e)
            return None
        else:
            if response.status_code == 429:
                logger.warning("Rate limit exceeded while fetching notifications.")
                return None
            if response.status_code == 200:
                return response.json()  # type: ignore[no-any-return]
            logger.warning("Failed to fetch notifications (HTTP %d): %s", response.status_code, response.text)
            return None

    def extract_download_url_from_notifications(
        self,
        notifications: dict[str, Any],
        started_after_ms: int = 0,
    ) -> str | None:
        """Extract the download URL from the most recent export-completed notification.

        Notion no longer returns enqueuedAt or exportURL in the getTasks
        response.  Instead the download link is delivered via an
        "export-completed" activity in getNotificationLogV2.

        We pick the most recent export-completed activity whose timestamp
        is >= ``started_after_ms`` (wall-clock milliseconds recorded before
        the export was triggered) to avoid picking up stale notifications
        from previous runs.

        Args:
            notifications: The notification data dictionary from
                getNotificationLogV2.
            started_after_ms: Only consider activities whose timestamp is
                at or after this value (epoch ms).  Defaults to 0 (accept
                any activity) for backwards compatibility with the recovery
                queue.

        Returns
        -------
            The download URL as a string, or None if not found.
        """
        notification_map = notifications.get("recordMap", {}).get("notification", {})

        # Collect all export-completed activities that happened after our
        # export was triggered.
        matching_activities: list[tuple[int, dict[str, Any]]] = []
        for activity_data in notifications.get("recordMap", {}).get("activity", {}).values():
            # Activity data is nested: activity_data -> value -> value
            activity_value = activity_data.get("value", {}).get("value", {})
            if activity_value.get("type") != "export-completed":
                continue
            try:
                activity_timestamp = int(activity_value.get("start_time", 0))
            except (ValueError, TypeError):
                continue
            # Only consider activities that happened after the export was
            # triggered (with a small 5-second tolerance to account for
            # clock skew between local machine and Notion servers).
            if activity_timestamp >= (started_after_ms - 5000):
                matching_activities.append((activity_timestamp, activity_value))

        if not matching_activities:
            logger.warning("No matching export-completed activities found")
            return None

        # Pick the most recent activity (highest timestamp)
        _, best_match_activity = max(matching_activities, key=lambda x: x[0])
        activity_id = best_match_activity.get("id")

        # Map activity to notification(s) so we can mark/archive it later
        matched_notification_ids = [
            notif_id
            for notif_id, notif_obj in notification_map.items()
            if notif_obj.get("value", {}).get("value", {}).get("activity_id") == activity_id
        ]
        msg = f"Notification IDs referencing selected activity_id {activity_id}: {matched_notification_ids}"
        logger.debug(msg)

        # The download link lives inside edits[0].link
        edits = best_match_activity.get("edits", [])
        if edits and edits[0].get("link"):
            self.export_notification_id = matched_notification_ids[0] if matched_notification_ids else None
            return str(edits[0]["link"])

        logger.warning("No download link found in edits")
        return None

    async def _download_file(self, download_url: str, temp_dir: Path) -> Path | None:
        """Download the export file with retry for transient errors.

        The session carries both the token_v2 and file_token cookies, matching
        what the Notion web app sends to file.notion.com.
        """
        timestamp = get_timestamp_string()
        flattened_suffix = "-flattened" if self.settings.flatten_export_filetree else ""
        filename = f"notion-export-{self.settings.export_type.value}{flattened_suffix}_{timestamp}.zip"

        file_path = temp_dir / filename

        max_retries = self.settings.max_retries
        base_delay = self.settings.retry_delay
        max_delay = self.settings.max_retry_delay

        for attempt in range(max_retries):
            try:
                logger.info("Downloading export file: %s (attempt %d/%d)", filename, attempt + 1, max_retries)

                response = self.session.get(
                    download_url,
                    stream=True,
                    timeout=self.settings.download_timeout,
                )
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                self._write_response_to_file(response, file_path, total_size)

                file_size = file_path.stat().st_size
                logger.info("Download completed: %s (%d bytes)", filename, file_size)
                return file_path

            except requests.HTTPError as e:
                status_code = e.response.status_code if e.response is not None else None
                if status_code == 403:
                    return self._handle_forbidden_download(e, download_url, file_path)
                if attempt < max_retries - 1 and status_code is not None and status_code >= 500:
                    delay = min(base_delay * (2**attempt), max_delay)
                    logger.warning(
                        "Download failed with HTTP %d (attempt %d/%d), retrying in %ds",
                        status_code,
                        attempt + 1,
                        max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.exception("Failed to download file")
                    return None

            except Exception:
                logger.exception("Failed to download file")
                return None

        return None

    def _write_response_to_file(self, response: requests.Response, file_path: Path, total_size: int) -> int:
        """Stream a response body to disk, returning the number of bytes written."""
        downloaded = 0
        with file_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

                    if downloaded % (10 * 1024 * 1024) == 0 and total_size > 0:
                        progress = (downloaded / total_size) * 100
                        logger.debug(
                            "Download progress: %.1f%% (%d/%d bytes)",
                            progress,
                            downloaded,
                            total_size,
                        )
        return downloaded

    def _handle_forbidden_download(
        self,
        error: requests.HTTPError,
        download_url: str,
        file_path: Path,
    ) -> Path | None:
        """Handle a 403 download: log the CDN's reason and try without cookies.

        Returns the downloaded file if the no-cookie fallback succeeds, else None.
        """
        if error.response is not None:
            logger.error(
                "Download forbidden (HTTP 403) for %s. Response body: %s",
                error.response.url,
                error.response.text[:500],
            )

        # The signed URL may be self-sufficient (the signature is the
        # authorization); an invalid file_token cookie can also cause a 403,
        # so try once without cookies.
        logger.warning("Retrying download without cookies...")
        fallback_file = self._download_without_cookies(download_url, file_path)
        if fallback_file is not None:
            logger.info("Download succeeded without cookies (file_token cookie was rejected)")
            return fallback_file

        logger.error(
            "Download forbidden (HTTP 403) with and without cookies. The NOTION_FILE_TOKEN cookie "
            "is likely expired or invalid, or the export link is bound to a different account. "
            "Refresh it: Notion -> DevTools -> Network -> any request -> Cookies -> file_token, "
            "then update .env and re-run to resume. If it still fails, start a fresh export with "
            "--skip-resume.",
        )
        return None

    def _download_without_cookies(self, download_url: str, file_path: Path) -> Path | None:
        """Attempt a download without any cookies.

        The signed export URL may be self-sufficient (the signature is the
        authorization), so a 403 with cookies does not necessarily mean the
        link is dead - an invalid file_token cookie can also cause a 403.
        """
        try:
            response = requests.get(
                download_url,
                stream=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) "
                        "Gecko/20100101 Firefox/139.0"
                    ),
                },
                timeout=self.settings.download_timeout,
            )
            response.raise_for_status()

            self._write_response_to_file(response, file_path, 0)
        except Exception:
            logger.debug("Download without cookies failed", exc_info=True)
            file_path.unlink(missing_ok=True)
            return None

        file_size = file_path.stat().st_size
        logger.info("Download completed without cookies: %s (%d bytes)", file_path.name, file_size)
        return file_path

    async def _update_notification(self, args: dict[str, Any], debug_action: str) -> bool:
        if not self.export_notification_id:
            logger.debug("No notification to update")
            return True
        msg = f"Updating notification {self.export_notification_id} with args: {args}"
        logger.debug(msg)

        operations = [
            {
                "command": "update",
                "pointer": {
                    "table": "notification",
                    "id": self.export_notification_id,
                    "spaceId": self.settings.notion_space_id,
                },
                "path": [],
                "args": args,
            },
        ]

        transaction_data = {
            "requestId": str(uuid.uuid4()),
            "transactions": [
                {
                    "id": str(uuid.uuid4()),
                    "spaceId": self.settings.notion_space_id,
                    "debug": {
                        "userAction": debug_action,
                    },
                    "operations": operations,
                },
            ],
        }

        try:
            response = self.session.post(
                self.MARK_READ_ENDPOINT,
                json=transaction_data,
                timeout=30,
            )
            response.raise_for_status()
        except Exception:
            logger.exception("Exception while updating notification")
            return False
        else:
            return True

    async def mark_notifications_as_read(self) -> bool:
        return await self._update_notification(
            {"read": True},
            "InboxActionsMenu.toggleNotificationReadStatus",
        )

    async def mark_notifications_as_unread(self) -> bool:
        return await self._update_notification(
            {"read": False, "visited": False},
            "InboxActionsMenu.toggleNotificationReadStatus",
        )

    async def mark_notification_as_archived(self) -> bool:
        return await self._update_notification(
            {
                "visited": True,
                "read": True,
                "archived_at": int(time.time() * 1000),
            },
            "InboxActionsMenu.handleArchive",
        )

    async def mark_notification_as_unarchived(self) -> bool:
        return await self._update_notification(
            {
                "visited": False,
                "archived_at": None,
            },
            "Activity.handleUnarchive",
        )
