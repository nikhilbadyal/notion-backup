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

from .client import NotionClient

logger = logging.getLogger(__name__)


class BackupManager:
    """Main backup manager that orchestrates the entire backup process."""

    def __init__(self, settings: Settings) -> None:
        """Initialize the backup manager."""
        self.settings = settings
        self.notion_client = NotionClient(settings)

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
        backup_file = None
        error_message = None

        try:
            logger.info("=" * 60)
            logger.info("Starting Notion Backup Process")
            logger.info("=" * 60)

            # Test connections first
            await self._test_connections(dry_run=dry_run)

            # Create temporary directory for download
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)

                # Export from Notion or create dummy file
                if dry_run:
                    logger.info("Step 1: Creating dummy export file (DRY RUN MODE)...")
                    backup_file = self._create_dummy_export(temp_path)
                else:
                    logger.info("Step 1: Exporting from Notion...")
                    backup_file = await self.notion_client.export_workspace(temp_path)

                    if not backup_file:
                        error_message = "Failed to export from Notion"
                        logger.error(error_message)
                        return False

                file_size = backup_file.stat().st_size
                logger.info("Export completed: %s (%s)", backup_file.name, format_file_size(file_size))

                # Store backup
                logger.info("Step 2: Storing backup...")
                storage_result = await self.storage.store(
                    backup_file,
                    destination_name=backup_file.name,
                )

                if not storage_result.success:
                    error_message = f"Failed to store backup: {storage_result.message}"
                    logger.error(error_message)
                    return False

                logger.info("Backup stored successfully: %s", storage_result.location or "Unknown location")

                # Cleanup old backups if configured
                if self.settings.max_local_backups:
                    logger.info("Step 3: Cleaning up old backups...")
                    cleanup_result = await self.storage.cleanup_old_backups(
                        self.settings.max_local_backups,
                    )

                    if cleanup_result.success:
                        logger.info("Cleanup completed: %s", cleanup_result.message)
                    else:
                        logger.warning("Cleanup failed: %s", cleanup_result.message)

                backup_success = True

                # Send success notification
                await self._send_success_notification(
                    backup_file,
                    storage_result.location or "Unknown location",
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

    async def _send_success_notification(self, backup_file: Path, storage_location: str, dry_run: bool = False) -> None:
        """Send success notification."""
        if not self.settings.enable_notifications:
            return

        try:
            file_size = backup_file.stat().st_size

            title = "Backup Completed Successfully" + (" (DRY RUN)" if dry_run else "")
            message = (
                f"Notion workspace backup completed successfully!\n\n"
                f"{'🧪 MODE: DRY RUN (Dummy Export File)' + chr(10)*2 if dry_run else ''}"
                f"📁 File: {backup_file.name}\n"
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
