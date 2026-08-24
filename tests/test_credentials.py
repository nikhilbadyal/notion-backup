"""Tests for Notion credential verification (test_connection) and pre-flight wiring."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from src.config import Settings
from src.core.backup import BackupManager
from src.core.client import NotionClient

SPACE_ID = "test-space-00000000-0000-0000-0000-000000000000"


def make_settings() -> Settings:
    """Build a Settings instance with minimal required Notion fields."""
    return Settings(
        notion_space_id=SPACE_ID,
        notion_token_v2=SecretStr("test-token-v2"),
        notion_file_token=SecretStr("test-file-token"),
    )


def make_client_with_mock_session() -> tuple[NotionClient, MagicMock]:
    """Build a NotionClient and return it along with its mocked session."""
    client = NotionClient(make_settings())
    session = MagicMock()
    client.session = session
    return client, session


# ---------------------------------------------------------------------------
# NotionClient.test_connection
# ---------------------------------------------------------------------------


class TestNotionConnection:
    """Verify NotionClient.test_connection handles API responses correctly."""

    def test_valid_credentials(self) -> None:
        """A 200 response containing the space ID should succeed."""
        client, session = make_client_with_mock_session()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"space": {SPACE_ID: {}}}
        session.post.return_value = response

        result = asyncio.run(client.test_connection())

        assert result.success is True
        assert SPACE_ID in result.message
        session.post.assert_called_once()

    def test_valid_credentials_user_keyed_response(self) -> None:
        """A 200 response keyed by user ID (real getSpaces shape) should succeed."""
        client, session = make_client_with_mock_session()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "user-00000000-0000-0000-0000-000000000000": {
                "space": {SPACE_ID: {}, "other-space": {}},
            },
        }
        session.post.return_value = response

        result = asyncio.run(client.test_connection())

        assert result.success is True
        assert SPACE_ID in result.message

    def test_invalid_token_returns_401(self) -> None:
        """A 401 response should report the token as invalid/expired."""
        client, session = make_client_with_mock_session()
        response = MagicMock()
        response.status_code = 401
        session.post.return_value = response

        result = asyncio.run(client.test_connection())

        assert result.success is False
        assert "401" in result.message

    def test_space_missing_from_response(self) -> None:
        """A 200 response without the configured space ID should fail."""
        client, session = make_client_with_mock_session()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"space": {"other-space": {}}}
        session.post.return_value = response

        result = asyncio.run(client.test_connection())

        assert result.success is False
        assert SPACE_ID in result.message

    def test_space_missing_from_user_keyed_response(self) -> None:
        """A user-keyed 200 response without the configured space ID should fail."""
        client, session = make_client_with_mock_session()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "user-00000000-0000-0000-0000-000000000000": {
                "space": {"other-space": {}},
            },
        }
        session.post.return_value = response

        result = asyncio.run(client.test_connection())

        assert result.success is False
        assert SPACE_ID in result.message

    def test_unexpected_status_code(self) -> None:
        """A non-200, non-401 status should fail with the status code."""
        client, session = make_client_with_mock_session()
        response = MagicMock()
        response.status_code = 500
        session.post.return_value = response

        result = asyncio.run(client.test_connection())

        assert result.success is False
        assert "500" in result.message

    def test_network_exception(self) -> None:
        """A network exception should fail gracefully without raising."""
        client, session = make_client_with_mock_session()
        session.post.side_effect = Exception("connection refused")

        result = asyncio.run(client.test_connection())

        assert result.success is False
        assert "connection refused" in result.message


# ---------------------------------------------------------------------------
# BackupManager._test_connections pre-flight wiring
# ---------------------------------------------------------------------------


class TestPreflightWiring:
    """Verify the backup pre-flight fails fast on invalid Notion credentials."""

    def test_notion_failure_raises_connection_error(self) -> None:
        """Invalid Notion credentials should raise ConnectionError before storage."""
        manager = BackupManager(make_settings())
        manager.notion_client = MagicMock()
        manager.notion_client.test_connection = AsyncMock(
            return_value=MagicMock(success=False, message="Notion token invalid or expired (HTTP 401)"),
        )
        manager.storage = MagicMock()
        manager.storage.test_connection = AsyncMock()

        with pytest.raises(ConnectionError, match="Notion credentials failed"):
            asyncio.run(manager._test_connections(dry_run=False))  # noqa: SLF001 - testing private pre-flight

        # Storage should not be tested once Notion credentials fail
        manager.storage.test_connection.assert_not_awaited()

    def test_notion_success_proceeds_to_storage(self) -> None:
        """Valid Notion credentials should proceed to storage testing."""
        manager = BackupManager(make_settings())
        manager.notion_client = MagicMock()
        manager.notion_client.test_connection = AsyncMock(
            return_value=MagicMock(success=True, message="Notion credentials valid"),
        )
        manager.storage = MagicMock()
        manager.storage.test_connection = AsyncMock(
            return_value=MagicMock(success=True, message="Local storage accessible"),
        )

        # Should not raise
        asyncio.run(manager._test_connections(dry_run=False))  # noqa: SLF001 - testing private pre-flight

        manager.storage.test_connection.assert_awaited_once()

    def test_dry_run_skips_notion_check(self) -> None:
        """Dry-run mode should skip the Notion API check entirely."""
        manager = BackupManager(make_settings())
        manager.notion_client = MagicMock()
        manager.notion_client.test_connection = AsyncMock()
        manager.storage = MagicMock()
        manager.storage.test_connection = AsyncMock(
            return_value=MagicMock(success=True, message="Local storage accessible"),
        )

        # Should not raise
        asyncio.run(manager._test_connections(dry_run=True))  # noqa: SLF001 - testing private pre-flight

        manager.notion_client.test_connection.assert_not_awaited()
        manager.storage.test_connection.assert_awaited_once()
