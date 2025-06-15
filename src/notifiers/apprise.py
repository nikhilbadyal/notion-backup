"""Apprise notification backend."""

from typing import Any

from .base import AbstractNotifier, NotificationLevel, NotificationResult

try:
    import apprise

    APPRISE_AVAILABLE = True
except ImportError:
    APPRISE_AVAILABLE = False
    apprise = None  # type: ignore[assignment]


class AppriseNotifier(AbstractNotifier):
    """Apprise notification backend."""

    def __init__(self, config: dict[str, Any], logger: Any = None) -> None:
        """Initialize Apprise notifier."""
        super().__init__(config, logger)
        self.urls = config.get("urls", [])
        self.title = config.get("title", "Notion Backup")
        self.enabled = config.get("enabled", False)

        # Try to initialize Apprise
        if APPRISE_AVAILABLE and apprise is not None:
            self.apprise = apprise.Apprise()
            self._add_urls()

            # Log initialization with masked URLs for security
            self.log("info", f"Apprise notifier initialized with {len(self.urls)} URLs")
            if self.urls:
                masked_urls = [self._mask_url(url) for url in self.urls]
                self.log("debug", f"Notification URLs (masked): {masked_urls}")
        else:
            self.log("error", "Apprise library not installed")
            self.apprise = None  # type: ignore[assignment]

    def _add_urls(self) -> None:
        """Add configured URLs to Apprise."""
        for url in self.urls:
            if url.strip():
                self.apprise.add(url.strip())

    def _mask_url(self, url: str) -> str:
        """Mask sensitive parts of notification URLs for logging."""
        import re

        # Mask tokens/passwords in URLs (keep first 4 and last 4 characters)
        patterns = [
            # Telegram: tgram://token/chat_id
            (r"(tgram://)[^/]{8,}(/)", r"\g<1>****\g<2>"),
            # Discord: discord://webhook_id/webhook_token
            (r"(discord://[^/]+/)[^/]{8,}", r"\g<1>****"),
            # Slack: slack://TokenA/TokenB/TokenC/channel
            (r"(slack://)[^/]{4}[^/]*?([^/]{4}/[^/]{4}[^/]*?[^/]{4}/[^/]{4}[^/]*?[^/]{4}/)", r"\g<1>****\g<2>"),
            # Email: mailto://user:pass@domain
            (r"(mailto://[^:]+:)[^@]{4,}(@)", r"\g<1>****\g<2>"),
            # Generic: any ://token pattern
            (r"(://[^:/]{4})[^:/]{4,}([^:/]{4})", r"\g<1>****\g<2>"),
        ]

        masked_url = url
        for pattern, replacement in patterns:
            masked_url = re.sub(pattern, replacement, masked_url)

        return masked_url

    def _get_notification_type(self, level: NotificationLevel) -> apprise.NotifyType:
        """Map notification level to Apprise type."""
        mapping = {
            NotificationLevel.SUCCESS: apprise.NotifyType.SUCCESS,
            NotificationLevel.ERROR: apprise.NotifyType.FAILURE,
            NotificationLevel.WARNING: apprise.NotifyType.WARNING,
            NotificationLevel.INFO: apprise.NotifyType.INFO,
        }
        return mapping.get(level, apprise.NotifyType.INFO)

    async def send_notification(
        self,
        title: str,
        message: str,
        level: NotificationLevel = NotificationLevel.INFO,
        **kwargs: Any,
    ) -> NotificationResult:
        """Send notification via Apprise."""
        if not self.enabled:
            return NotificationResult(success=True, message="Notifications disabled")

        if not self.urls:
            return NotificationResult(success=False, message="No notification URLs configured")

        try:
            # Get notification type
            notify_type = self._get_notification_type(level)

            # Prepare title with prefix
            full_title = f"{self.title}: {title}" if title else self.title

            # Send notification
            result = self.apprise.notify(
                body=message,
                title=full_title,
                notify_type=notify_type,
                **kwargs,
            )

            if result:
                success_msg = f"Notification sent successfully via {len(self.urls)} service(s)"
                self.log("info", success_msg)
                return NotificationResult(
                    success=True,
                    message=success_msg,
                    sent_count=len(self.urls),
                )
            error_msg = "Failed to send notifications"
            self.log("error", error_msg)
            return NotificationResult(success=False, message=error_msg)

        except Exception as e:
            error_msg = f"Failed to send notification: {e}"
            self.log("error", error_msg)
            return NotificationResult(success=False, message=error_msg)

    async def test_connection(self) -> NotificationResult:
        """Test Apprise notification services."""
        if not self.enabled:
            return NotificationResult(success=True, message="Notifications disabled")

        if not self.urls:
            return NotificationResult(success=False, message="No notification URLs configured")

        try:
            # Send test notification
            test_result = await self.send_notification(
                title="Test Notification",
                message="This is a test notification to verify your configuration is working.",
                level=NotificationLevel.INFO,
            )

            if test_result.success:
                return NotificationResult(
                    success=True,
                    message=f"Test notification sent successfully to {len(self.urls)} service(s)",
                    sent_count=len(self.urls),
                )
            return NotificationResult(
                success=False,
                message=f"Test notification failed: {test_result.message}",
            )

        except Exception as e:
            error_msg = f"Failed to test notification connection: {e}"
            self.log("error", error_msg)
            return NotificationResult(success=False, message=error_msg)

    def add_url(self, url: str) -> bool:
        """Add a new notification URL."""
        try:
            result = self.apprise.add(url)
            if result:
                self.urls.append(url)
                self.log("info", f"Added notification URL: {url}")

        except Exception as e:
            self.log("error", f"Failed to add notification URL: {e}")
            return False
        else:
            return result

    def clear_urls(self) -> None:
        """Clear all notification URLs."""
        self.apprise.clear()
        self.urls.clear()
        self.log("info", "Cleared all notification URLs")

    def get_urls(self) -> list[str]:
        """Get list of configured notification URLs."""
        return list(self.urls)
