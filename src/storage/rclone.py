"""Rclone storage backend for remote cloud storage."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .base import AbstractStorage, StorageResult


class RcloneStorage(AbstractStorage):
    """Rclone storage backend for remote cloud storage."""

    def __init__(self, config: dict[str, Any], logger: Any = None) -> None:
        """Initialize rclone storage with configuration."""
        super().__init__(config, logger)
        self.remote = config.get("remote")
        self.remote_path = config.get("path", "notion-backups")
        self.config_path = config.get("config_path")
        self.additional_args = config.get("additional_args", [])
        self.keep_local = config.get("keep_local", True)

        if not self.remote:
            msg = "Rclone remote name is required"
            raise ValueError(msg)

        self.log("info", f"Rclone storage initialized for remote: {self.remote}")
        if self.remote_path:
            self.log("info", f"Remote path: {self.remote_path}")

    def _build_rclone_cmd(self, operation: str, *args: str) -> list[str]:
        """Build rclone command with common options."""
        cmd = ["rclone", operation]

        if self.config_path:
            cmd.extend(["--config", str(self.config_path)])

        cmd.extend(args)
        cmd.extend(self.additional_args)

        return cmd

    async def _run_rclone_cmd(self, cmd: list[str], log_output: bool = True) -> tuple[bool, str]:
        """Run rclone command asynchronously."""
        try:
            # Mask sensitive parts of the command for logging
            masked_cmd = self._mask_command_for_logging(cmd)
            self.log("debug", f"Running command: {' '.join(masked_cmd)}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            stdout_str = stdout.decode("utf-8").strip()
            stderr_str = stderr.decode("utf-8").strip()

            if process.returncode == 0:
                if log_output and stdout_str:
                    # For some commands (like lsd, lsjson), we want to log the output
                    # For others (like about, mkdir), we don't need to show full output
                    if cmd[1] in ["lsd", "lsjson"]:
                        self.log("debug", f"Command succeeded: {stdout_str}")
                    else:
                        self.log("debug", "Command succeeded")
                else:
                    self.log("debug", "Command succeeded")
                return True, stdout_str
            error_msg = f"Command failed with code {process.returncode}: {stderr_str}"
            self.log("error", error_msg)

        except Exception as e:
            error_msg = f"Failed to run rclone command: {e}"
            self.log("error", error_msg)
            return False, error_msg
        else:
            return False, error_msg

    def _mask_command_for_logging(self, cmd: list[str]) -> list[str]:
        """Mask sensitive parts of rclone commands for safe logging."""
        masked_cmd = []
        for i, arg in enumerate(cmd):
            # Mask config path to just show filename
            if arg == "--config" and i + 1 < len(cmd):
                masked_cmd.append(arg)
                config_path = cmd[i + 1]
                # Show only the filename, not the full path
                masked_cmd.append(f".../{config_path.split('/')[-1]}")
            elif "--config" in cmd and i > 0 and cmd[i - 1] == "--config":
                # Skip this as it was already added above
                continue
            else:
                masked_cmd.append(arg)
        return masked_cmd

    async def store(self, file_path: Path, destination_name: str | None = None) -> StorageResult:
        """Store a file using rclone."""
        try:
            if not file_path.exists():
                return StorageResult(
                    success=False,
                    message=f"Source file does not exist: {file_path}",
                )

            # Build destination path
            dest_name = destination_name or file_path.name
            remote_dest = f"{self.remote}:{self.remote_path}"

            # Copy file to remote directory (rclone copy copies files TO a directory)
            cmd = self._build_rclone_cmd("copy", str(file_path), remote_dest)
            success, output = await self._run_rclone_cmd(cmd)

            if not success:
                return StorageResult(
                    success=False,
                    message=f"Failed to upload to rclone: {output}",
                )

            file_size = file_path.stat().st_size
            final_location = f"{remote_dest}/{dest_name}"
            self.log("info", f"File uploaded successfully to: {final_location}")
            self.log("info", f"File size: {file_size:,} bytes")

            # Remove local file if configured
            if not self.keep_local:
                try:
                    file_path.unlink()
                    self.log("info", f"Removed local file: {file_path}")
                except Exception as e:
                    self.log("warning", f"Failed to remove local file: {e}")

            return StorageResult(
                success=True,
                message=f"File uploaded successfully to {final_location}",
                location=final_location,
                size=file_size,
            )

        except Exception as e:
            error_msg = f"Failed to store file via rclone: {e}"
            self.log("error", error_msg)
            return StorageResult(success=False, message=error_msg)

    async def list_backups(self) -> list[dict[str, Any]]:
        """List backups in rclone remote."""
        try:
            remote_path = f"{self.remote}:{self.remote_path}"
            cmd = self._build_rclone_cmd("lsjson", remote_path)

            success, output = await self._run_rclone_cmd(cmd)

            if not success:
                self.log("warning", f"Failed to list remote backups: {output}")
                return []

            if not output.strip():
                return []

            backups = []
            files_data = json.loads(output)

            for file_info in files_data:
                if file_info.get("IsDir", False):
                    continue

                name = file_info.get("Name", "")
                if name.startswith("notion-export-") and name.endswith(".zip"):
                    # Parse modification time
                    mod_time_str = file_info.get("ModTime", "")
                    try:
                        mod_time = datetime.fromisoformat(mod_time_str.replace("Z", "+00:00"))
                    except Exception:
                        mod_time = datetime.now(tz=UTC)

                    backups.append(
                        {
                            "name": name,
                            "path": f"{remote_path}/{name}",
                            "size": file_info.get("Size", 0),
                            "created": mod_time,
                            "modified": mod_time,
                        },
                    )

            # Sort by modification time, newest first
            backups.sort(key=lambda x: x["modified"], reverse=True)

            self.log("info", f"Found {len(backups)} remote backups")

        except Exception as e:
            self.log("error", f"Failed to list remote backups: {e}")
            return []
        else:
            return backups

    async def cleanup_old_backups(self, keep_count: int) -> StorageResult:
        """Clean up old remote backup files."""
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
                    # Build remote file path
                    remote_file = f"{self.remote}:{self.remote_path}/{backup['name']}"
                    cmd = self._build_rclone_cmd("delete", remote_file)

                    success, output = await self._run_rclone_cmd(cmd)

                    if success:
                        removed_size += backup["size"]
                        removed_count += 1
                        self.log("info", f"Removed old backup: {backup['name']}")
                    else:
                        self.log("warning", f"Failed to remove {backup['name']}: {output}")

                except Exception as e:
                    self.log("warning", f"Failed to remove {backup['name']}: {e}")

            message = f"Cleaned up {removed_count} old remote backups, freed {removed_size:,} bytes"
            self.log("info", message)

            return StorageResult(success=True, message=message)

        except Exception as e:
            error_msg = f"Failed to cleanup old remote backups: {e}"
            self.log("error", error_msg)
            return StorageResult(success=False, message=error_msg)

    async def test_connection(self) -> StorageResult:
        """Test rclone remote connection."""
        try:
            # Test remote accessibility using 'about' command which shows remote info without listing contents
            # This is more private and less verbose than 'lsd'
            remote_path = f"{self.remote}:"
            cmd = self._build_rclone_cmd("about", remote_path)

            success, output = await self._run_rclone_cmd(cmd, log_output=False)

            if success:
                # Parse the about output to get basic info without exposing directory contents
                lines = output.split("\n")
                total_info = ""
                for line in lines:
                    if "Total:" in line or "Used:" in line or "Free:" in line:
                        total_info += line.strip() + " "

                if total_info:
                    return StorageResult(
                        success=True,
                        message=f"Rclone remote '{self.remote}' is accessible ({total_info.strip()})",
                    )
                return StorageResult(
                    success=True,
                    message=f"Rclone remote '{self.remote}' is accessible",
                )
            # If 'about' fails, fallback to a more targeted check
            # Try to create/access the specific backup directory instead of listing root
            if self.remote_path:
                backup_path = f"{self.remote}:{self.remote_path}"
                cmd = self._build_rclone_cmd("mkdir", backup_path)
                success, _ = await self._run_rclone_cmd(cmd, log_output=False)

                if success:
                    return StorageResult(
                        success=True,
                        message=f"Rclone remote '{self.remote}' and backup path '{self.remote_path}' are accessible",
                    )

            return StorageResult(
                success=False,
                message=f"Rclone remote '{self.remote}' is not accessible: {output}",
            )

        except Exception as e:
            error_msg = f"Failed to test rclone connection: {e}"
            return StorageResult(success=False, message=error_msg)
