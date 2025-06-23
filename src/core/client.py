"""Notion API client for exporting workspaces."""

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from src.config import Settings
from src.utils import get_timestamp_string, retry_async

logger = logging.getLogger(__name__)


# noinspection PyBroadException
class NotionClient:
    """Client for interacting with Notion's export API."""

    BASE_URL = "https://www.notion.so/api"
    API_VERSION = "v3"
    ENQUEUE_ENDPOINT = f"{BASE_URL}/{API_VERSION}/enqueueTask"
    GET_TASKS_ENDPOINT = f"{BASE_URL}/{API_VERSION}/getTasks"
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

        # Set up session with default headers
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0",
                "x-notion-space-id": self.settings.notion_space_id,
                "Cookie": f"{self.TOKEN_V2}={self.settings.notion_token_v2.get_secret_value()}",
                "Content-Type": self.CONTENT_TYPE,
            },
        )

        logger.info("Notion client initialized")

    @retry_async(max_retries=3, delay=5.0)
    async def export_workspace(self, temp_dir: Path) -> Path | None:
        """
        Export the Notion workspace and return the path to the downloaded file.

        Args:
            temp_dir: Temporary directory for download

        Returns
        -------
            Path to the downloaded file or None if failed
        """
        try:
            # Phase 1: Trigger export task
            task_id = await self._trigger_export_task()
            if not task_id:
                logger.error("Failed to trigger export task")
                return None

            logger.debug("Export task triggered successfully with task ID: %s", task_id)

            # Phase 2: Poll for task completion
            enqueued_at = await self._poll_task_completion(task_id)
            if not enqueued_at:
                logger.error("Failed to get task completion status")
                return None

            logger.info("Export task completed at timestamp: %s", enqueued_at)

            # Phase 3: Fetch notifications and extract download URL
            max_retries = 20
            base_delay = 5
            max_delay = 60
            download_url = None

            for attempt in range(max_retries):
                if attempt > 0:
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.info("Waiting %d seconds before retry %d/%d...", delay, attempt + 1, max_retries)
                    await asyncio.sleep(delay)

                notifications = await self.get_notifications()
                if notifications:
                    msg = f"Revived {len(notifications.get('notificationIds', {}))} notifications."
                    logger.debug(msg)
                    download_url = self.extract_download_url_from_notifications(notifications, enqueued_at)
                    if download_url:
                        logger.info("Download URL obtained")
                        break
                else:
                    logger.info("No notifications received on attempt %d", attempt + 1)

            if not download_url:
                logger.error("Failed to extract download URL")
                return None
            # Phase 4: Download file
            return await self._download_file(download_url, temp_dir)

        except Exception:
            logger.exception("Failed to export workspace")
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

    async def _poll_task_completion(self, task_id: str) -> int | None:
        """Poll for task completion and return the enqueuedAt timestamp."""
        task_data = {"taskIds": [task_id]}
        max_wait_time = 1200  # 20 minutes
        check_interval = 10
        elapsed_time = 0

        while elapsed_time < max_wait_time:
            result = await self._poll_once(task_data)
            if result is not None:
                return result
            await asyncio.sleep(check_interval)
            elapsed_time += check_interval
            logger.info("Waiting for export task to complete... (%d seconds)", elapsed_time)

        logger.error("Export task did not complete within %d seconds", max_wait_time)
        return None

    async def _poll_once(self, task_data: dict[str, Any]) -> int | None:
        """Poll Notion for task status once. Returns enqueuedAt if success/failure/invalid, None to continue polling."""
        try:
            response = self.session.post(
                self.GET_TASKS_ENDPOINT,
                json=task_data,
                timeout=30,
            )

            if response.status_code == 429:
                logger.warning("Rate limit exceeded during polling.")
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
                enqueued_at = task_result.get("equeuedAt")
                if enqueued_at:
                    logger.info("Task completed successfully. EnqueuedAt: %s", enqueued_at)
                    return int(enqueued_at)
                logger.warning("Task completed but no enqueuedAt timestamp found")
                return None
            if task_state == "failure":
                logger.error("Export task failure")
            logger.info("Task state: %s. Continuing to poll...", task_state)

        except Exception as e:
            logger.warning("Error polling task status: %s", e)
        return None

    async def get_notifications(self) -> dict[str, Any] | None:  # type: ignore[return]
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
            if response.status_code == 429:
                logger.warning("Rate limit exceeded while fetching notifications.")
                return None
            if response.status_code == 200:
                return response.json()  # type: ignore[no-any-return]
            logger.warning("Failed to fetch notifications (HTTP %d): %s", response.status_code, response.text)
        except Exception as e:
            logger.warning("Exception fetching notifications: %s", e)
        else:
            return None

    def extract_download_url_from_notifications(self, notifications: dict[str, Any], enqueued_at: int) -> str | None:
        """
        Extract download URL from notifications using enqueuedAt timestamp.

        Args:
            notifications: The notification data dictionary.
            enqueued_at: The timestamp when the export task was enqueued.

        Returns
        -------
            The download URL as a string, or None if not found.
        """
        notification_map = notifications.get("recordMap", {}).get("notification", {})

        # Find all export-completed activities after enqueued_at
        matching_activities = []
        for activity_data in notifications.get("recordMap", {}).get("activity", {}).values():
            activity_value = activity_data.get("value", {})
            if activity_value.get("type") == "export-completed":
                try:
                    activity_timestamp = int(activity_value.get("start_time", 0))
                except (ValueError, TypeError):
                    continue
                time_diff = activity_timestamp - enqueued_at
                if time_diff >= 0:
                    matching_activities.append((time_diff, activity_value))

        if not matching_activities:
            logger.warning("No matching export-completed activities found")
            return None

        # Choose the nearest activity
        _, best_match_activity = sorted(matching_activities, key=lambda x: abs(x[0]))[0]
        activity_id = best_match_activity.get("id")

        # Map activity to notification(s)
        matched_notification_ids = [
            notif_id
            for notif_id, notif_obj in notification_map.items()
            if notif_obj.get("value", {}).get("activity_id") == activity_id
        ]
        msg = f"Notification IDs referencing selected activity_id {activity_id}: {matched_notification_ids}"
        logger.debug(msg)

        edits = best_match_activity.get("edits", [])
        if edits and edits[0].get("link"):
            self.export_notification_id = matched_notification_ids[0] if matched_notification_ids else None
            return str(edits[0]["link"])

        logger.warning("No download link found in edits")
        return None

    async def _download_file(self, download_url: str, temp_dir: Path) -> Path | None:
        """Download the export file."""
        try:
            self.session.headers.update(
                {"Cookie": f"{self.FILE_TOKEN}={self.settings.notion_file_token.get_secret_value()}"},
            )

            # Generate filename
            timestamp = get_timestamp_string()
            flattened_suffix = "-flattened" if self.settings.flatten_export_filetree else ""
            filename = f"notion-export-{self.settings.export_type.value}{flattened_suffix}_{timestamp}.zip"

            file_path = temp_dir / filename

            logger.info("Downloading export file: %s", filename)

            response = self.session.get(
                download_url,
                stream=True,
                timeout=self.settings.download_timeout,
            )
            response.raise_for_status()

            # Download with progress
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0

            with file_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Log progress every 10MB
                        if downloaded % (10 * 1024 * 1024) == 0 and total_size > 0:
                            progress = (downloaded / total_size) * 100
                            logger.debug("Download progress: %.1f%% (%d/%d bytes)", progress, downloaded, total_size)

            file_size = file_path.stat().st_size
            logger.info("Download completed: %s (%d bytes)", filename, file_size)

        except Exception:
            logger.exception("Failed to download file")
            return None
        else:
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
            if response.status_code != 200:
                msg = f"Failed to update notification (HTTP {response.status_code}): {response.text}"
                logger.warning(
                    msg,
                )
                return False
            self.export_notification_id = None
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
