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
    QUEUE_UPDATE_RETRIES = 5

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
        """Add a pending export task to the recovery queue.

        Args:
            task_id: The ID of the export task.
            enqueued_at: Wall-clock time (epoch ms) when the export was
                triggered.  Used to filter stale notifications during
                recovery.  (Historically this was a Notion-provided
                timestamp, but Notion no longer returns it.)
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

        task_id = export_data.get("task_id", "unknown")
        retry_count = int(export_data.get("retry_count", 0))
        payload = json.dumps(export_data)

        for transaction_attempt in range(1, self.QUEUE_UPDATE_RETRIES + 1):
            try:
                updated, stored_retry_count = self._queue_export_transaction(task_id, retry_count, payload)
            except redis.exceptions.WatchError:
                logger.debug(
                    "Recovery queue changed while updating task %s; retrying transaction (%d/%d)",
                    task_id,
                    transaction_attempt,
                    self.QUEUE_UPDATE_RETRIES,
                )
            except Exception:
                logger.exception("Failed to push pending export to Redis")
                return
            else:
                if updated:
                    logger.info("Queued pending export task in Redis: %s (retry %d)", task_id, retry_count)
                else:
                    logger.info(
                        "Task %s already in recovery queue (retry %d), skipping stale retry %d",
                        task_id,
                        stored_retry_count,
                        retry_count,
                    )
                return

        logger.warning(
            "Recovery queue remained busy; task %s was not queued after %d atomic attempts",
            task_id,
            self.QUEUE_UPDATE_RETRIES,
        )

    def _queue_export_transaction(self, task_id: Any, retry_count: int, payload: str) -> tuple[bool, int]:
        """Atomically insert a task or replace it with higher retry metadata."""
        if self.client is None:
            msg = "Redis connection disappeared before queue transaction"
            raise RuntimeError(msg)

        # WATCH makes the read/compare/write decision atomic across concurrent
        # backup processes sharing the recovery queue.
        with self.client.pipeline() as pipe:
            pipe.watch(self.RECOVERY_QUEUE_KEY)
            existing_items = pipe.lrange(self.RECOVERY_QUEUE_KEY, 0, -1)
            matching_index: int | None = None
            existing_retry_count = -1

            for index, item in enumerate(existing_items):
                try:
                    existing = json.loads(item)
                except json.JSONDecodeError:
                    # Preserve malformed entries so queue repair remains a
                    # separate, explicit operation.
                    continue
                if isinstance(existing, dict) and existing.get("task_id") == task_id:
                    matching_index = index
                    try:
                        existing_retry_count = int(existing.get("retry_count", 0))
                    except (TypeError, ValueError):
                        # Treat invalid legacy metadata as the initial attempt
                        # so a valid retry can replace it.
                        existing_retry_count = 0
                    break

            if matching_index is not None and existing_retry_count >= retry_count:
                # Commit an empty transaction so Redis still verifies that the
                # matching entry was not removed between LRANGE and this decision.
                pipe.multi()
                pipe.execute()
                return False, existing_retry_count

            pipe.multi()
            if matching_index is None:
                pipe.rpush(self.RECOVERY_QUEUE_KEY, payload)
            else:
                # Replace stale metadata in place so queue ordering is stable
                # while retry progress moves monotonically forward.
                pipe.lset(self.RECOVERY_QUEUE_KEY, matching_index, payload)
            pipe.execute()
            return True, retry_count

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
