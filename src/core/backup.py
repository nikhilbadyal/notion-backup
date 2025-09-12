"""Main backup manager that orchestrates the backup process."""

import asyncio
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from src.config import Settings
from src.notifiers import AbstractNotifier, AppriseNotifier
from src.storage import AbstractStorage, LocalStorage, RcloneStorage
from src.utils import format_file_size, get_timestamp_string
from src.utils.redis_client import RedisClient

from .client import NotionClient

logger = logging.getLogger(__name__)


class BackupManager:
    """Main backup manager that orchestrates the entire backup process."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the backup manager."""
        self.settings = settings
        self.notion_client = NotionClient(settings)
        self.redis_client = RedisClient(settings)

        # Initialize storage backend
        self.storage = self._create_storage_backend()

        # Initialize notifier
        self.notifier = self._create_notifier()

        logger.info("Backup manager initialized")
        logger.info("Storage backend: %s", self.settings.storage_backend.value)
        logger.info("Notifications: %s", "enabled" if self.settings.enable_notifications else "disabled")

        # Show masked notification URLs for debugging (if enabled and URLs exist)
        if self.settings.enable_notifications and self.settings.apprise_urls:
            masked_urls = self.settings.get_masked_apprise_urls()
            logger.debug("Notification URLs (masked): %s", masked_urls)

    def _create_dummy_export(self, temp_path: Path) -> Path:
        """Create a dummy export file for dry run mode."""
        timestamp = get_timestamp_string()
        flattened_suffix = "-flattened" if self.settings.flatten_export_filetree else ""
        filename = f"notion-export-{self.settings.export_type.value}{flattened_suffix}_{timestamp}.zip"

        dummy_file = temp_path / filename

        # Create a dummy ZIP file with some content
        with zipfile.ZipFile(dummy_file, "w") as zipf:
            # Add a dummy README file
            readme_content = f"""# Dummy Notion Export (DRY RUN)

This is a dummy export file created for testing purposes.

- Export Type: {self.settings.export_type.value}
- Flattened: {self.settings.flatten_export_filetree}
- Comments: {self.settings.export_comments}
- Timestamp: {timestamp}
- Storage Backend: {self.settings.storage_backend.value}

This file was created by the dry-run mode to test storage and notification
functionality without making actual Notion API calls.
"""
            zipf.writestr("README.md", readme_content)

            # Add a dummy page content
            page_content = f"""# Sample Notion Page

This is a sample page content that would normally come from your Notion workspace.

Created in dry-run mode at {timestamp}.
"""
            zipf.writestr("Sample Page.md", page_content)

        logger.info("Created dummy export file: %s (%d bytes)", filename, dummy_file.stat().st_size)
        return dummy_file

    def _create_storage_backend(self) -> AbstractStorage:
        """Create the appropriate storage backend."""
        storage_config = self.settings.get_storage_config()

        if self.settings.storage_backend.value == "local":
            return LocalStorage(storage_config, logger)
        if self.settings.storage_backend.value == "rclone":
            return RcloneStorage(storage_config, logger)
        msg = f"Unsupported storage backend: {self.settings.storage_backend}"
        raise ValueError(msg)

    def _create_notifier(self) -> AbstractNotifier:
        """Create the notifier."""
        notification_config = self.settings.get_notification_config()
        return AppriseNotifier(notification_config, logger)

    async def run_backup(self, dry_run: bool = False) -> bool:
        """
        Run the complete backup process.

        Returns
        -------
            True if backup was successful, False otherwise
        """
        backup_success = False
        error_message = None

        try:
            logger.info("=" * 60)
            logger.info("Starting Notion Backup Process")
            logger.info("=" * 60)

            # Test connections first
            await self._test_connections(dry_run=dry_run)

            # Process recovery queue
            if self.settings.redis_host:
                await self._process_recovery_queue()

            # Create temporary directory for download
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Step 1: Export from Notion
                backup_file = await self._handle_export(temp_path, dry_run)
                if not backup_file:
                    return False

                # Get file size before it's potentially deleted
                file_size = backup_file.stat().st_size

                # Step 1.5: Mark notifications as read
                await self._handle_notification_marking(dry_run)

                # Step 2: Store backup
                storage_location = await self._handle_storage(backup_file)
                if not storage_location:
                    return False

                # Step 2.5: Archive notification
                await self._handle_notification_archiving(dry_run)

                # Step 3: Cleanup old backups
                await self._handle_cleanup()

                # Send success notification
                await self._send_success_notification(
                    backup_file.name,
                    file_size,
                    storage_location,
                    dry_run=dry_run,
                )

                logger.info("=" * 60)
                logger.info("Backup Process Completed Successfully")
                logger.info("=" * 60)

                return True

        except Exception as e:
            error_message = f"Unexpected error during backup: {e}"
            logger.exception(error_message)
            backup_success = False

        finally:
            # Send failure notification if needed
            if not backup_success and error_message:
                await self._send_error_notification(error_message)

        return backup_success

    async def _process_recovery_queue(self) -> None:
        """Process any pending exports from the Redis recovery queue."""
        logger.info("Checking for pending exports in recovery queue...")
        pending_exports = self.redis_client.get_pending_exports()

        if not pending_exports:
            logger.info("No pending exports found.")
            return

        logger.info("Found %d pending exports. Processing...", len(pending_exports))
        successful_recoveries = 0

        for export in pending_exports:
            if await self._process_single_recovery(export):
                successful_recoveries += 1

        if successful_recoveries > 0:
            logger.info("Successfully recovered %d pending exports", successful_recoveries)

    async def _process_single_recovery(self, export: dict[str, Any]) -> bool:
        """
        Process a single pending export recovery.

        Args:
            export: Export data containing task_id, enqueued_at, and retry_count

        Returns
        -------
            True if recovery was successful, False otherwise
        """
        task_id = export.get("task_id")
        enqueued_at = export.get("enqueued_at")
        retry_count = export.get("retry_count", 0)

        if not task_id or not enqueued_at:
            logger.warning("Invalid pending export data: %s", export)
            return False

        # Skip exports that have exceeded retry limit
        if retry_count >= 3:
            logger.warning("Export task %s exceeded retry limit (%d), removing from queue", task_id, retry_count)
            return False

        logger.info("Processing pending export task: %s (attempt %d)", task_id, retry_count + 1)

        recovery_successful = await self._attempt_export_recovery(task_id, enqueued_at)

        if not recovery_successful:
            await self._handle_failed_recovery(task_id, enqueued_at, retry_count)

        return recovery_successful

    async def _attempt_export_recovery(self, task_id: str, enqueued_at: int) -> bool:
        """
        Attempt to recover a single export by checking notifications and downloading.

        Args:
            task_id: The export task ID
            enqueued_at: When the task was enqueued

        Returns
        -------
            True if recovery was successful, False otherwise
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            notifications = await self.notion_client.get_notifications()

            if not notifications:
                logger.warning("No notifications found for recovered task: %s", task_id)
                return False

            download_url = self.notion_client.extract_download_url_from_notifications(
                notifications,
                enqueued_at,
            )

            if not download_url:
                logger.warning("Could not find download URL for recovered task: %s", task_id)
                return False

            backup_file = await self.notion_client._download_file(download_url, temp_path)  # noqa: SLF001

            if not backup_file:
                logger.error("Failed to download recovered backup for task: %s", task_id)
                return False

            await self._handle_storage(backup_file)
            await self._handle_notification_marking(dry_run=False)
            await self._handle_notification_archiving(dry_run=False)
            logger.info("Successfully recovered and stored backup for task: %s", task_id)
            return True

    async def _handle_failed_recovery(self, task_id: str, enqueued_at: int, retry_count: int) -> None:
        """
        Handle a failed recovery attempt by re-queuing or discarding.

        Args:
            task_id: The export task ID
            enqueued_at: When the task was enqueued
            retry_count: Current retry count
        """
        retry_count += 1
        if retry_count < 3:
            logger.info("Re-queuing failed recovery for task %s (retry %d/3)", task_id, retry_count)
            export_with_retry = {
                "task_id": task_id,
                "enqueued_at": enqueued_at,
                "retry_count": retry_count,
            }
            self.redis_client.push_pending_export_with_retry(export_with_retry)
        else:
            logger.error("Task %s failed recovery after 3 attempts, discarding", task_id)

    async def _handle_export(self, temp_path: Path, dry_run: bool) -> Path | None:
        """Handle the export process and return the backup file path."""
        backup_file: Path | None = None

        if dry_run:
            logger.info("Step 1: Creating dummy export file (DRY RUN MODE)...")
            backup_file = self._create_dummy_export(temp_path)
        else:
            logger.info("Step 1: Exporting from Notion...")
            backup_file = await self.notion_client.export_workspace(temp_path)

            if not backup_file:
                logger.error("Failed to export from Notion")
                return None

        # At this point backup_file is guaranteed to be Path, not None
        if backup_file is None:
            logger.error("Unexpected: backup_file is None after successful export")
            return None

        file_size = backup_file.stat().st_size
        logger.info("Export completed: %s (%s)", backup_file.name, format_file_size(file_size))
        return backup_file

    async def _handle_notification_marking(self, dry_run: bool) -> None:
        """Handle marking export notifications as read."""
        if not dry_run and self.settings.mark_notifications_as_read:
            logger.info("Step 1.5: Marking export notifications as read...")
            mark_read_success = await self.notion_client.mark_notifications_as_read()
            if mark_read_success:
                logger.info("✅ Export notifications marked as read")
            else:
                logger.warning("⚠️ Failed to mark notifications as read (continuing with backup)")

    async def _handle_notification_archiving(self, dry_run: bool) -> None:
        """Handle archiving the export notification."""
        if not dry_run and self.settings.archive_notification:
            logger.info("Step 2.5: Archiving export notification...")
            archive_success = await self.notion_client.mark_notification_as_archived()
            if archive_success:
                logger.info("✅ Export notification archived")
            else:
                logger.warning("⚠️ Failed to archive notification (continuing with backup)")

    async def _handle_storage(self, backup_file: Path) -> str | None:
        """Handle storing the backup file and return the storage location."""
        logger.info("Step 2: Storing backup...")
        storage_result = await self.storage.store(
            backup_file,
            destination_name=backup_file.name,
        )

        if not storage_result.success:
            logger.error("Failed to store backup: %s", storage_result.message)
            return None

        storage_location = storage_result.location or "Unknown location"
        logger.info("Backup stored successfully: %s", storage_location)
        return storage_location

    async def _handle_cleanup(self) -> None:
        """Handle cleanup of old backups if configured."""
        if self.settings.max_backups:
            logger.info("Step 3: Cleaning up old backups...")
            cleanup_result = await self.storage.cleanup_old_backups(
                self.settings.max_backups,
            )

            if cleanup_result.success:
                logger.info("Cleanup completed: %s", cleanup_result.message)
            else:
                logger.warning("Cleanup failed: %s", cleanup_result.message)

    async def _test_connections(self, dry_run: bool = False) -> None:
        """Test all connections before starting backup."""
        if dry_run:
            logger.info("Testing connections (DRY RUN MODE - skipping Notion API)...")
        else:
            logger.info("Testing connections...")

        # Test storage connection
        storage_test = await self.storage.test_connection()
        if storage_test.success:
            logger.info("✓ Storage connection: %s", storage_test.message)
        else:
            logger.error("✗ Storage connection failed: %s", storage_test.message)
            msg = f"Storage connection failed: {storage_test.message}"
            raise ConnectionError(msg)

        # Test notification connection if enabled
        if self.settings.enable_notifications:
            notification_test = await self.notifier.test_connection()
            if notification_test.success:
                logger.info("✓ Notification connection: %s", notification_test.message)
            else:
                logger.warning("⚠ Notification connection failed: %s", notification_test.message)

    async def _send_success_notification(
        self,
        backup_filename: str,
        file_size: int,
        storage_location: str,
        dry_run: bool = False,
    ) -> None:
        """Send success notification."""
        if not self.settings.enable_notifications:
            return

        try:
            title = "Backup Completed Successfully" + (" (DRY RUN)" if dry_run else "")
            message = (
                f"Notion workspace backup completed successfully!\n\n"
                f"{'🧪 MODE: DRY RUN (Dummy Export File)' + chr(10)*2 if dry_run else ''}"
                f"📁 File: {backup_filename}\n"
                f"📊 Size: {format_file_size(file_size)}\n"
                f"🏪 Storage: {self.settings.storage_backend.value}\n"
                f"📍 Location: {storage_location}\n"
                f"🕐 Export Type: {self.settings.export_type.value}"
            )

            await self.notifier.send_success(title, message)

        except Exception as e:
            logger.warning("Failed to send success notification: %s", e)

    async def _send_error_notification(self, error_message: str) -> None:
        """Send error notification."""
        if not self.settings.enable_notifications:
            return

        try:
            title = "Backup Failed"
            message = (
                f"Notion workspace backup failed!\n\n"
                f"❌ Error: {error_message}\n"
                f"🕐 Please check the logs for more details."
            )

            await self.notifier.send_error(title, message)

        except Exception as e:
            logger.warning("Failed to send error notification: %s", e)

    async def list_backups(self) -> list[dict[str, Any]]:
        """List available backups."""
        try:
            return await self.storage.list_backups()
        except Exception:
            logger.exception("Failed to list backups")
            return []

    async def cleanup_backups(self, keep_count: int) -> bool:
        """Clean up old backups."""
        try:
            result = await self.storage.cleanup_old_backups(keep_count)
            if result.success:
                logger.info("Cleanup completed: %s", result.message)
                return True
            logger.error("Cleanup failed: %s", result.message)

        except Exception:
            logger.exception("Failed to cleanup backups")
            return False
        else:
            return False


# Synchronous wrapper for async operations
def run_backup_sync(settings: Settings, dry_run: bool = False) -> bool:
    """Run backup synchronously (wrapper for async operation)."""
    try:
        manager = BackupManager(settings)
        return asyncio.run(manager.run_backup(dry_run=dry_run))
    except Exception:
        logger.exception("Failed to run backup")
        return False


def list_backups_sync(settings: Settings) -> list[dict[str, Any]]:
    """List backups synchronously (wrapper for async operation)."""
    try:
        manager = BackupManager(settings)
        return asyncio.run(manager.list_backups())
    except Exception:
        logger.exception("Failed to list backups")
        return []


def cleanup_backups_sync(settings: Settings, keep_count: int) -> bool:
    """Clean up backups synchronously (wrapper for async operation)."""
    try:
        manager = BackupManager(settings)
        return asyncio.run(manager.cleanup_backups(keep_count))
    except Exception:
        logger.exception("Failed to cleanup backups")
        return False
