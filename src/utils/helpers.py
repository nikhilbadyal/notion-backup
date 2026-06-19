"""Helper utility functions."""

import asyncio
import functools
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)

    while size >= 1024.0 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1

    return f"{size:.1f} {size_names[i]}"


def get_timestamp_string(dt: datetime | None = None) -> str:
    """Get timestamp string in consistent format."""
    if dt is None:
        dt = datetime.now(UTC)
    return dt.strftime("%Y-%m-%d_%H-%M-%S")


def retry_async(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator for retrying async functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception: Exception | None = None
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e

                    if attempt == max_retries:
                        break

                    # Wait before retrying
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff

            # If we get here, all retries failed
            if last_exception is not None:
                raise last_exception
            msg = "Retry failed without exception"
            raise RuntimeError(msg)

        return wrapper

    return decorator


def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")
    return filename.strip()


def truncate_string(text: str, max_length: int = 100, suffix: str = "...") -> str:
    """Truncate string to maximum length with suffix."""
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


SESSION_FILE = Path.home() / ".notion-resume.json"


def save_session(task_id: str, started_at_ms: int) -> None:
    """Save export session state for resumption."""
    data = {"task_id": task_id, "export_started_at_ms": started_at_ms}
    SESSION_FILE.write_text(json.dumps(data, indent=2))
    logging.getLogger(__name__).info("Session saved for task %s", task_id)


def load_session() -> dict[str, Any] | None:
    """Load saved session state, if any."""
    if not SESSION_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text())
        task_id = data.get("task_id")
        started_at_ms = data.get("export_started_at_ms")
        if task_id and started_at_ms:
            return {"task_id": task_id, "export_started_at_ms": started_at_ms}
        clear_session()
        return None
    except (json.JSONDecodeError, OSError):
        clear_session()
        return None


def clear_session() -> None:
    """Clear the saved session state."""
    try:
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
            logging.getLogger(__name__).info("Session cleared")
    except OSError:
        pass
