"""Redis client for managing pending export recovery."""

import json
import logging
from typing import Any

import redis

from src.config import Settings

logger = logging.getLogger(__name__)


class RedisClient:
    """A client for interacting with Redis for export recovery."""

    RECOVERY_QUEUE_KEY = "notion_backup_recovery_queue"

    def __init__(self, settings: Settings) -> None:
        """
        Initialize the Redis client.

        Args:
            settings: The application settings.
        """
        self.settings = settings
        self.client = self._get_redis_client()

    def _get_redis_client(self) -> redis.Redis | None:
        """Create and connect to a Redis client if configured."""
        if not self.settings.redis_host:
            return None

        try:
            password = self.settings.redis_password.get_secret_value() if self.settings.redis_password else None
            username = self.settings.redis_username
            ssl_params = {}
            if self.settings.redis_ssl:
                ssl_params = {
                    "ssl": True,
                    "ssl_cert_reqs": self.settings.redis_ssl_cert_reqs,
                }
                if self.settings.redis_ssl_ca_certs:
                    ssl_params["ssl_ca_certs"] = str(self.settings.redis_ssl_ca_certs)

            client = redis.Redis(
                host=self.settings.redis_host,
                port=self.settings.redis_port,
                db=self.settings.redis_db,
                username=username,
                password=password,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                **ssl_params,
            )
            client.ping()
            logger.info("Successfully connected to Redis.")
        except redis.exceptions.ConnectionError:
            logger.exception("Failed to connect to Redis")
            return None
        except Exception:
            logger.exception("An unexpected error occurred with Redis")
            return None
        else:
            return client

    def _ensure_connection(self) -> bool:
        """Ensure Redis connection is available, attempt reconnection if needed."""
        if not self.client:
            return False

        try:
            self.client.ping()
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            logger.warning("Redis connection lost, attempting to reconnect...")
            self.client = self._get_redis_client()
            return self.client is not None
        else:
            return True

    def push_pending_export(self, task_id: str, enqueued_at: int) -> None:
        """
        Add a pending export task to the recovery queue.

        Args:
            task_id: The ID of the export task.
            enqueued_at: The timestamp when the task was enqueued.
        """
        export_data = {
            "task_id": task_id,
            "enqueued_at": enqueued_at,
            "retry_count": 0,
        }
        self.push_pending_export_with_retry(export_data)

    def push_pending_export_with_retry(self, export_data: dict[str, Any]) -> None:
        """
        Add a pending export task to the recovery queue with retry information.

        Args:
            export_data: Dictionary containing task_id, enqueued_at, and retry_count.
        """
        if not self._ensure_connection() or not self.client:
            logger.warning("Redis not available, cannot push pending export")
            return

        try:
            payload = json.dumps(export_data)
            self.client.rpush(self.RECOVERY_QUEUE_KEY, payload)
            task_id = export_data.get("task_id", "unknown")
            retry_count = export_data.get("retry_count", 0)
            logger.info("Pushed pending export task to Redis recovery queue: %s (retry %d)", task_id, retry_count)
        except Exception:
            logger.exception("Failed to push pending export to Redis")

    def get_pending_exports(self) -> list[dict[str, Any]]:
        """
        Retrieve all pending export tasks from the recovery queue.
        Uses atomic operation to prevent data loss.

        Returns
        -------
            A list of pending export tasks.
        """
        if not self._ensure_connection() or not self.client:
            logger.warning("Redis not available, cannot retrieve pending exports")
            return []

        try:
            # Use atomic operation to move items from queue to processing
            pipe = self.client.pipeline()
            pipe.lrange(self.RECOVERY_QUEUE_KEY, 0, -1)
            pipe.delete(self.RECOVERY_QUEUE_KEY)
            results = pipe.execute()

            items = results[0] if results else []
            if not items:
                return []

            pending_tasks = [json.loads(item) for item in items]
            logger.info("Retrieved %d pending export tasks from Redis.", len(pending_tasks))

        except Exception:
            logger.exception("Failed to retrieve pending exports from Redis")
            return []
        else:
            return pending_tasks

    def remove_pending_export(self, task_id: str) -> None:
        """
        Remove a specific pending export from the queue.

        Args:
            task_id: The ID of the export task to remove.
        """
        if not self._ensure_connection() or not self.client:
            return

        try:
            # Get all items and filter out the one to remove
            items = self.client.lrange(self.RECOVERY_QUEUE_KEY, 0, -1)
            filtered_items = []

            for item in items:
                try:
                    data = json.loads(item)
                    if data.get("task_id") != task_id:
                        filtered_items.append(item)
                except json.JSONDecodeError:
                    # Keep malformed items to avoid data loss
                    filtered_items.append(item)

            # Replace queue with filtered items
            pipe = self.client.pipeline()
            pipe.delete(self.RECOVERY_QUEUE_KEY)
            if filtered_items:
                pipe.rpush(self.RECOVERY_QUEUE_KEY, *filtered_items)
            pipe.execute()

            logger.info("Removed pending export task from Redis: %s", task_id)
        except Exception:
            logger.exception("Failed to remove pending export from Redis")
