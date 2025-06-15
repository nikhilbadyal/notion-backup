"""Local file system storage backend."""

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import AbstractStorage, StorageResult


class LocalStorage(AbstractStorage):
    """Local file system storage backend."""

    def __init__(self, config: dict[str, Any], logger: Any = None) -> None:
        """Initialize local storage with configuration."""
        super().__init__(config, logger)
        self.path = Path(config.get("path", "./downloads"))
        self.max_backups = config.get("max_backups")

        # Ensure directory exists
        self.path.mkdir(parents=True, exist_ok=True)
        self.log("info", f"Local storage initialized at: {self.path}")

    async def store(self, file_path: Path, destination_name: str | None = None) -> StorageResult:
        """Store a file locally."""
        try:
            if not file_path.exists():
                return StorageResult(
                    success=False,
                    message=f"Source file does not exist: {file_path}",
                )

            # Use provided name or keep original
            dest_name = destination_name or file_path.name
            dest_path = self.path / dest_name

            # Copy file
            shutil.copy2(file_path, dest_path)
            file_size = dest_path.stat().st_size

            self.log("info", f"File stored locally at: {dest_path}")
            self.log("info", f"File size: {file_size:,} bytes")

            return StorageResult(
                success=True,
                message=f"File stored successfully at {dest_path}",
                location=str(dest_path),
                size=file_size,
            )

        except Exception as e:
            error_msg = f"Failed to store file locally: {e}"
            self.log("error", error_msg)
            return StorageResult(success=False, message=error_msg)

    async def list_backups(self) -> list[dict[str, Any]]:
        """List local backup files."""
        try:
            backups = []
            pattern = "notion-export-*.zip"

            for file_path in self.path.glob(pattern):
                if file_path.is_file():
                    stat = file_path.stat()
                    backups.append(
                        {
                            "name": file_path.name,
                            "path": str(file_path),
                            "size": stat.st_size,
                            "created": datetime.fromtimestamp(stat.st_ctime, tz=UTC),
                            "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                        },
                    )

            # Sort by creation time, newest first
            backups.sort(key=lambda x: x["created"], reverse=True)  # type: ignore[arg-type,return-value]

            self.log("info", f"Found {len(backups)} local backups")

        except Exception as e:
            self.log("error", f"Failed to list backups: {e}")
            return []
        else:
            return backups

    async def cleanup_old_backups(self, keep_count: int) -> StorageResult:
        """Clean up old local backup files."""
        try:
            backups = await self.list_backups()

            if len(backups) <= keep_count:
                return StorageResult(
                    success=True,
                    message=f"No cleanup needed. Found {len(backups)} backups, keeping {keep_count}",
                )

            # Remove old backups
            to_remove = backups[keep_count:]
            removed_count = 0
            removed_size = 0

            for backup in to_remove:
                try:
                    file_path = Path(backup["path"])
                    if file_path.exists():
                        removed_size += backup["size"]
                        file_path.unlink()
                        removed_count += 1
                        self.log("info", f"Removed old backup: {backup['name']}")
                except Exception as e:
                    self.log("warning", f"Failed to remove {backup['name']}: {e}")

            message = f"Cleaned up {removed_count} old backups, freed {removed_size:,} bytes"
            self.log("info", message)

            return StorageResult(success=True, message=message)

        except Exception as e:
            error_msg = f"Failed to cleanup old backups: {e}"
            self.log("error", error_msg)
            return StorageResult(success=False, message=error_msg)

    async def test_connection(self) -> StorageResult:
        """Test local storage accessibility."""
        try:
            # Test write access
            test_file = self.path / ".test_write"
            test_file.write_text("test")
            test_file.unlink()

            return StorageResult(
                success=True,
                message=f"Local storage accessible at {self.path}",
            )

        except Exception as e:
            error_msg = f"Local storage not accessible: {e}"
            return StorageResult(success=False, message=error_msg)
