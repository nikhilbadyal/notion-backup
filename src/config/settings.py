"""Configuration settings for Notion Backup."""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExportType(str, Enum):
    """Supported export formats."""

    MARKDOWN = "markdown"
    HTML = "html"


class StorageBackend(str, Enum):
    """Supported storage backends."""

    LOCAL = "local"
    RCLONE = "rclone"


class NotificationLevel(str, Enum):
    """Notification levels."""

    SUCCESS = "success"
    ERROR = "error"
    ALL = "all"
    NONE = "none"


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_parse_none_str="null",  # Handle null strings
        env_parse_enums=True,  # Parse enums
    )

    # Notion API Configuration
    notion_space_id: str = Field(..., description="Notion workspace ID")
    notion_token_v2: SecretStr = Field(..., description="Notion authentication token")
    notion_file_token: SecretStr = Field(..., description="Notion file download token")

    # Export Configuration
    export_type: ExportType = Field(default=ExportType.MARKDOWN, description="Export format")
    flatten_export_filetree: bool = Field(default=False, description="Flatten nested page structure")
    export_comments: bool = Field(default=True, description="Include comments in export")
    time_zone: str = Field(default="Asia/Kolkata", description="Timezone for export timestamps")

    # Storage Configuration
    storage_backend: StorageBackend = Field(default=StorageBackend.LOCAL, description="Storage backend to use")
    local_path: Path = Field(default=Path("./downloads"), description="Local storage path")

    # Rclone Configuration
    rclone_remote: str | None = Field(default=None, description="Rclone remote name")
    rclone_path: str | None = Field(default=None, description="Path on rclone remote")
    rclone_config_path: Path | None = Field(default=None, description="Path to rclone config file")
    rclone_additional_args: Any = Field(
        default_factory=list,
        description="Additional rclone arguments",
    )

    # Notification Configuration
    enable_notifications: bool = Field(default=False, description="Enable notifications")
    notification_level: NotificationLevel = Field(default=NotificationLevel.ALL, description="Notification level")
    apprise_urls: Any = Field(default_factory=list, description="Apprise notification URLs")
    notification_title: str = Field(default="Notion Backup", description="Notification title")

    # Retry Configuration
    max_retries: int = Field(default=3, description="Maximum number of retries")
    retry_delay: int = Field(default=5, description="Delay between retries in seconds")
    download_timeout: int = Field(default=300, description="Download timeout in seconds")

    # Cleanup Configuration
    keep_local_backup: bool = Field(default=True, description="Keep local backup after uploading to remote")
    max_local_backups: int | None = Field(default=None, description="Maximum number of local backups to keep")

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, v: Path) -> Path:
        """Ensure local path is absolute."""
        return v.resolve()

    @field_validator("rclone_config_path", mode="before")
    @classmethod
    def validate_rclone_config_path(cls, v: str | Path | None) -> Path | None:
        """Ensure rclone config path is absolute and expand ~ if provided."""
        if v is None:
            return None
        if isinstance(v, str):
            # Expand ~ to home directory
            v = Path(v).expanduser()
        elif isinstance(v, Path):
            v = v.expanduser()
        return v.resolve()

    @field_validator("apprise_urls", mode="before")
    @classmethod
    def validate_apprise_urls(cls, v: Any) -> list[str]:
        """Handle comma-separated URLs from environment variables."""
        if isinstance(v, str):
            if not v.strip():
                return []

            v_stripped = v.strip()

            # Reject JSON array format - we only support comma-separated
            if v_stripped.startswith("[") and v_stripped.endswith("]"):
                msg = "JSON array format is not supported for APPRISE_URLS. Use comma-separated format: url1,url2,url3"
                raise ValueError(
                    msg,
                )

            # Parse comma-separated URLs
            urls = []
            for url in v_stripped.split(","):
                cleaned_url = url.strip().strip("\"'")  # Remove quotes only
                if cleaned_url:
                    urls.append(cleaned_url)
            return urls

        if isinstance(v, list):
            # Already a list - convert to strings
            return [str(url) for url in v]

        return []

    @field_validator("rclone_additional_args", mode="before")
    @classmethod
    def validate_rclone_additional_args(cls, v: Any) -> list[str]:
        """Handle comma-separated arguments from environment variables."""
        if isinstance(v, str):
            if not v.strip():
                return []

            v_stripped = v.strip()

            # Reject JSON array format - we only support comma-separated
            if v_stripped.startswith("[") and v_stripped.endswith("]"):
                msg = (
                    "JSON array format is not supported for RCLONE_ADDITIONAL_ARGS. "
                    "Use comma-separated format: --verbose,--transfers=8"
                )
                raise ValueError(
                    msg,
                )

            # Parse comma-separated arguments
            args = []
            for arg in v_stripped.split(","):
                cleaned_arg = arg.strip().strip("\"'")  # Remove quotes only
                if cleaned_arg:
                    args.append(cleaned_arg)
            return args

        if isinstance(v, list):
            # Already a list - convert to strings
            return [str(arg) for arg in v]

        return []

    def get_storage_config(self) -> dict[str, Any]:
        """Get storage-specific configuration."""
        if self.storage_backend == StorageBackend.LOCAL:
            return {
                "path": self.local_path,
                "max_backups": self.max_local_backups,
            }
        if self.storage_backend == StorageBackend.RCLONE:
            return {
                "remote": self.rclone_remote,
                "path": self.rclone_path,
                "config_path": self.rclone_config_path,
                "additional_args": self.rclone_additional_args,
                "keep_local": self.keep_local_backup,
            }
        return {}

    def get_notification_config(self) -> dict[str, Any]:
        """Get notification-specific configuration."""
        return {
            "enabled": self.enable_notifications,
            "level": self.notification_level,
            "urls": self.apprise_urls,
            "title": self.notification_title,
        }

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

    def get_masked_apprise_urls(self) -> list[str]:
        """Get masked apprise URLs for safe logging."""
        return [self._mask_url(url) for url in self.apprise_urls]
