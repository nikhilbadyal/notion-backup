"""Abstract base class for storage backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class StorageResult:
    """Result of a storage operation."""

    success: bool
    message: str
    location: str | None = None
    size: int | None = None


class AbstractStorage(ABC):
    """Abstract base class for storage backends."""

    def __init__(self, config: dict[str, Any], logger: Any = None) -> None:
        """Initialize storage backend with configuration."""
        self.config = config
        self.logger = logger

    @abstractmethod
    async def store(self, file_path: Path, destination_name: str | None = None) -> StorageResult:
        """
        Store a file to the backend.

        Args:
            file_path: Path to the local file to store
            destination_name: Optional custom name for the stored file

        Returns
        -------
            StorageResult with operation details
        """

    @abstractmethod
    async def list_backups(self) -> list[dict[str, Any]]:
        """
        List available backups.

        Returns
        -------
            List of dictionaries containing backup metadata
        """

    @abstractmethod
    async def cleanup_old_backups(self, keep_count: int) -> StorageResult:
        """
        Clean up old backups, keeping only the specified number.

        Args:
            keep_count: Number of recent backups to keep

        Returns
        -------
            StorageResult with cleanup details
        """

    @abstractmethod
    async def test_connection(self) -> StorageResult:
        """
        Test the storage backend connection.

        Returns
        -------
            StorageResult indicating connection status
        """

    def log(self, level: str, message: str) -> None:
        """Log a message if logger is available."""
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(message)
