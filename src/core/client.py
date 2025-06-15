"""Notion API client for exporting workspaces."""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import requests

from src.config import Settings
from src.utils import get_timestamp_string, retry_async

logger = logging.getLogger(__name__)


class NotionClient:
    """Client for interacting with Notion's export API."""

    ENQUEUE_ENDPOINT = "https://www.notion.so/api/v3/enqueueTask"
    GET_TASKS_ENDPOINT = "https://www.notion.so/api/v3/getTasks"
    NOTIFICATION_ENDPOINT = "https://www.notion.so/api/v3/getNotificationLogV2"

    TOKEN_V2 = "token_v2"  # noqa: S105
    FILE_TOKEN = "file_token"  # noqa: S105

    def __init__(self, settings: Settings) -> None:
        """Initialize the Notion client."""
        self.settings = settings
        self.session = requests.Session()

        # Set up session with default headers
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0",
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

            logger.info("Export task triggered successfully with task ID: %s", task_id)

            # Phase 2: Poll for task completion
            enqueued_at = await self._poll_task_completion(task_id)
            if not enqueued_at:
                logger.error("Failed to get task completion status")
                return None

            logger.info("Export task completed at timestamp: %s", enqueued_at)

            # Phase 3: Extract download URL from notifications
            download_url = await self._extract_download_url(enqueued_at)
            if not download_url:
                logger.error("Failed to extract download URL")
                return None

            logger.info("Download URL obtained")

            # Phase 4: Download file
            return await self._download_file(download_url, temp_dir)

        except Exception:
            logger.exception("Failed to export workspace")
            return None

    async def _trigger_export_task(self) -> str | None:
        """Trigger the export task and return the task ID."""
        headers = {
            "Cookie": f"{self.TOKEN_V2}={self.settings.notion_token_v2.get_secret_value()}",
            "Content-Type": "application/json",
        }

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
                    headers=headers,
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
        headers = {
            "Cookie": f"{self.TOKEN_V2}={self.settings.notion_token_v2.get_secret_value()}",
            "Content-Type": "application/json",
        }

        task_data = {
            "taskIds": [task_id],
        }

        # Wait for export to complete (up to 10 minutes)
        max_wait_time = 1200  # 20 minutes
        check_interval = 10  # Check every 10 seconds
        elapsed_time = 0

        while elapsed_time < max_wait_time:
            try:
                response = self.session.post(
                    self.GET_TASKS_ENDPOINT,
                    headers=headers,
                    json=task_data,
                    timeout=30,
                )

                if response.status_code == 429:
                    logger.warning("Rate limit exceeded during polling.")
                    return None

                if response.status_code == 200:
                    data = response.json()
                    logger.debug("Task polling response: \n%s", json.dumps(data, indent=2))

                    results = data.get("results", [])
                    if results:
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
                            return None
                        logger.info("Task state: %s. Continuing to poll...", task_state)

                # Wait before next check
                await asyncio.sleep(check_interval)
                elapsed_time += check_interval

                logger.info("Waiting for export task to complete... (%d seconds)", elapsed_time)

            except Exception as e:
                logger.warning("Error polling task status: %s", e)
                await asyncio.sleep(check_interval)
                elapsed_time += check_interval

        logger.error("Export task did not complete within %d seconds", max_wait_time)
        return None

    async def _extract_download_url(self, enqueued_at: int) -> str | None:  # noqa: C901,PLR0912,PLR0915
        """Extract download URL from notifications using enqueuedAt timestamp."""
        headers = {
            "Cookie": f"{self.TOKEN_V2}={self.settings.notion_token_v2.get_secret_value()}",
            "Content-Type": "application/json",
        }

        notification_data = {
            "spaceId": self.settings.notion_space_id,
            "size": 20,
            "type": "unread_and_read",
            "variant": "no_grouping",
        }

        max_retries = 20
        base_delay = 5
        max_delay = 60

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.info("Waiting %d seconds before retry %d/%d...", delay, attempt + 1, max_retries)
                    await asyncio.sleep(delay)

                response = self.session.post(
                    self.NOTIFICATION_ENDPOINT,
                    headers=headers,
                    json=notification_data,
                    timeout=30,
                )

                if response.status_code == 429:
                    logger.warning(
                        "Rate limit exceeded while fetching notifications (attempt %d/%d).",
                        attempt + 1,
                        max_retries,
                    )
                    return None

                activity = {}
                if response.status_code == 200:
                    data = response.json()
                    logger.debug("Notification response received:\n%s", data)

                    record_map = data.get("recordMap", {})
                    activity = record_map.get("activity", {})

                    if not activity:
                        if attempt < max_retries - 1:
                            logger.info("No activities found yet, will retry...")
                            continue
                        logger.warning("No activity found in notifications after %d attempts", max_retries)
                        return None

                # Collect matching activities
                matching_activities = []
                all_export_activities = []

                for activity_data in activity.values():
                    activity_value = activity_data.get("value", {})
                    activity_type = activity_value.get("type")
                    activity_id = activity_value.get("id")

                    if activity_type == "export-completed":
                        activity_timestamp = int(activity_value.get("start_time", 0))
                        time_diff = activity_timestamp - enqueued_at
                        readable_time = datetime.fromtimestamp(activity_timestamp / 1000, tz=UTC).isoformat()

                        all_export_activities.append(
                            {
                                "id": activity_id,
                                "timestamp": activity_timestamp,
                                "time_diff": time_diff,
                                "readable": readable_time,
                            },
                        )

                        if time_diff >= 0:
                            matching_activities.append(
                                {
                                    "activity": activity_value,
                                    "timestamp": activity_timestamp,
                                    "time_diff": time_diff,
                                },
                            )
                            logger.debug(
                                "✅ Candidate export-completed activity: ID=%s, Time=%s, Diff=%s ms",
                                activity_id,
                                readable_time,
                                time_diff,
                            )
                        else:
                            logger.debug(
                                "⏪ Skipping export-completed activity before enqueued time: "
                                "ID=%s, Time=%s, Diff=%s ms",
                                activity_id,
                                readable_time,
                                time_diff,
                            )

                if not matching_activities and all_export_activities:
                    logger.warning(
                        "Found %d export-completed activities but none after enqueued_at (%s)",
                        len(all_export_activities),
                        datetime.fromtimestamp(enqueued_at / 1000, tz=UTC).isoformat(),
                    )
                    for act in all_export_activities:
                        logger.info("Activity ID=%s, Time=%s, Diff=%s ms", act["id"], act["readable"], act["time_diff"])

                best_match_activity = None
                if matching_activities:
                    matching_activities.sort(key=lambda x: (x["time_diff"] < 0, abs(x["time_diff"])))
                    best_match_activity = matching_activities[0]["activity"]

                    logger.info(
                        "🎯 Selected activity ID=%s with time diff: %s ms (from %d candidates)",
                        best_match_activity.get("id"),
                        matching_activities[0]["time_diff"],
                        len(matching_activities),
                    )

                    if matching_activities[0]["time_diff"] > 300000:  # more than 5 minutes late
                        logger.warning("⚠️ Best match activity is more than 5 minutes after enqueue time!")

                if best_match_activity:
                    edits = best_match_activity.get("edits", [])
                    if edits:
                        download_link = edits[0].get("link")
                        if download_link:
                            logger.info("✅ Successfully extracted download URL")
                            return str(download_link)
                        logger.warning("❌ No download link found in activity edits")
                    else:
                        logger.warning("❌ No edits found in matching activity")
                else:
                    logger.warning(
                        "❌ No matching export-completed activity found for enqueued_at %s",
                        datetime.fromtimestamp(enqueued_at / 1000, tz=UTC).isoformat(),
                    )

            except Exception as e:
                logger.warning("💥 Exception during download URL extraction: %s", e)

        logger.error("🚫 Exhausted all retries without finding a valid download URL")
        return None

    async def _download_file(self, download_url: str, temp_dir: Path) -> Path | None:
        """Download the export file."""
        try:
            headers = {}
            if self.settings.notion_file_token:
                headers["Cookie"] = f"{self.FILE_TOKEN}={self.settings.notion_file_token.get_secret_value()}"

            # Generate filename
            timestamp = get_timestamp_string()
            flattened_suffix = "-flattened" if self.settings.flatten_export_filetree else ""
            filename = f"notion-export-{self.settings.export_type.value}{flattened_suffix}_{timestamp}.zip"

            file_path = temp_dir / filename

            logger.info("Downloading export file: %s", filename)

            response = self.session.get(
                download_url,
                headers=headers,
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
                            logger.info("Download progress: %.1f%% (%d/%d bytes)", progress, downloaded, total_size)

            file_size = file_path.stat().st_size
            logger.info("Download completed: %s (%d bytes)", filename, file_size)

        except Exception:
            logger.exception("Failed to download file")
            return None
        else:
            return file_path
