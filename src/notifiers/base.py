"""Abstract base class for notification backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class NotificationLevel(str, Enum):
    """Notification levels."""

    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class NotificationResult:
    """Result of a notification operation."""

    success: bool
    message: str
    sent_count: int = 0


class AbstractNotifier(ABC):
    """Abstract base class for notification backends."""

    def __init__(self, config: dict[str, Any], logger: Any = None) -> None:
        """
        Initialize notifier with configuration.

        Args:
            config: Notification configuration dictionary
            logger: Optional logger instance
        """
        self.config = config
        self.logger = logger
        self.enabled = config.get("enabled", False)

    @abstractmethod
    async def send_notification(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        **kwargs: Any,
    ) -> NotificationResult:
        """
        Send a notification.

        Args:
            title: Notification title
            message: Notification message
            level: Notification level/severity
            **kwargs: Additional notification parameters

        Returns
        -------
            NotificationResult with operation details
        """

    @abstractmethod
    async def test_connection(self) -> NotificationResult:
        """
        Test the notification backend connection.

        Returns
        -------
            NotificationResult indicating connection status
        """

    async def send_success(self, title: str, message: str, **kwargs: Any) -> NotificationResult:
        """Send a success notification."""
        if not self.enabled:
            return NotificationResult(success=True, message="Notifications disabled")
        return await self.send_notification(title, message, NotificationLevel.SUCCESS, **kwargs)

    async def send_error(self, title: str, message: str, **kwargs: Any) -> NotificationResult:
        """Send an error notification."""
        if not self.enabled:
            return NotificationResult(success=True, message="Notifications disabled")
        return await self.send_notification(title, message, NotificationLevel.ERROR, **kwargs)

    async def send_warning(self, title: str, message: str, **kwargs: Any) -> NotificationResult:
        """Send a warning notification."""
        if not self.enabled:
            return NotificationResult(success=True, message="Notifications disabled")
        return await self.send_notification(title, message, NotificationLevel.WARNING, **kwargs)

    async def send_info(self, title: str, message: str, **kwargs: Any) -> NotificationResult:
        """Send an info notification."""
        if not self.enabled:
            return NotificationResult(success=True, message="Notifications disabled")
        return await self.send_notification(title, message, NotificationLevel.INFO, **kwargs)

    def log(self, level: str, message: str) -> None:
        """Log a message if logger is available."""
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(message)
