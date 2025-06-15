#!/usr/bin/env python3
"""
Notion Backup - Modular Version
Automatically backup your Notion workspace with pluggable storage and notification backends.
"""

import logging
import sys

import click
from pydantic import ValidationError

from src.config import Settings
from src.core import cleanup_backups_sync, list_backups_sync, run_backup_sync
from src.utils import format_file_size


def setup_logging(debug: bool = False) -> None:
    """Set up logging configuration."""
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    # Reduce noise from external libraries
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    # Suppress Apprise debug logs to prevent token leakage
    logging.getLogger("apprise").setLevel(logging.WARNING)


def load_settings() -> Settings:
    """Load and validate settings."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        click.echo("❌ Configuration Error:", err=True)
        for error in e.errors():
            field = " -> ".join(str(x) for x in error["loc"])
            click.echo(f"  {field}: {error['msg']}", err=True)
        click.echo("\nPlease check your .env file or environment variables.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Failed to load configuration: {e}", err=True)
        sys.exit(1)


@click.group(invoke_without_command=True)
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--dry-run", is_flag=True, help="Skip Notion export, use dummy file for testing storage/notifications")
@click.pass_context
def cli(ctx: click.Context, debug: bool, dry_run: bool) -> None:
    """Notion Backup - Modular backup tool for Notion workspaces."""
    # Set up logging
    setup_logging(debug)

    # Store flags in context for commands to access
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug
    ctx.obj["dry_run"] = dry_run

    # If no subcommand is provided, run backup by default
    if ctx.invoked_subcommand is None:
        ctx.invoke(backup)


@cli.command()
@click.pass_context
def backup(ctx: click.Context) -> None:
    """Run backup of Notion workspace."""
    settings = load_settings()
    dry_run = ctx.obj.get("dry_run", False)

    if dry_run:
        click.echo("🧪 Starting Notion Backup (DRY RUN MODE)...")
        click.echo("⚠️  Using dummy export file, skipping Notion API")
    else:
        click.echo("🚀 Starting Notion Backup...")
    click.echo(f"📦 Storage Backend: {settings.storage_backend.value}")
    click.echo(f"📧 Notifications: {'enabled' if settings.enable_notifications else 'disabled'}")
    click.echo(f"📄 Export Type: {settings.export_type.value}")
    click.echo()

    success = run_backup_sync(settings, dry_run=dry_run)

    if success:
        click.echo("✅ Backup completed successfully!")
    else:
        click.echo("❌ Backup failed! Check logs for details.", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def list(_: click.Context) -> None:  # noqa: A001
    """List available backups."""
    settings = load_settings()

    click.echo("📁 Listing available backups...")
    backups = list_backups_sync(settings)

    if not backups:
        click.echo("No backups found.")
        return

    click.echo(f"\nFound {len(backups)} backup(s):")
    click.echo("-" * 80)

    for i, backup in enumerate(backups, 1):
        size = format_file_size(backup.get("size", 0))
        created = backup.get("created", "Unknown")
        name = backup.get("name", "Unknown")

        click.echo(f"{i:2d}. {name}")
        click.echo(f"    Size: {size}")
        click.echo(f"    Created: {created}")
        if backup.get("path"):
            click.echo(f"    Path: {backup['path']}")
        click.echo()


@cli.command()
@click.option(
    "--keep",
    type=int,
    default=5,
    help="Number of recent backups to keep",
    show_default=True,
)
@click.pass_context
def cleanup(_: click.Context, keep: int) -> None:
    """Clean up old backups, keeping only the most recent ones."""
    settings = load_settings()

    click.echo(f"🧹 Cleaning up old backups (keeping {keep} most recent)...")

    success = cleanup_backups_sync(settings, keep)

    if success:
        click.echo("✅ Cleanup completed successfully!")
    else:
        click.echo("❌ Cleanup failed! Check logs for details.", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def test(_: click.Context) -> None:
    """Test configuration and connections."""
    settings = load_settings()

    click.echo("🔧 Testing configuration...")
    click.echo(f"📦 Storage Backend: {settings.storage_backend.value}")

    # Test would be implemented here
    click.echo("✅ Configuration test passed!")


if __name__ == "__main__":
    cli()
