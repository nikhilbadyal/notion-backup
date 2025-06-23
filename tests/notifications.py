import asyncio
import json

from src.config import Settings
from src.core.client import NotionClient


async def print_notification_status(client: NotionClient, notif_id: str, label: str) -> None:
    client.export_notification_id = notif_id
    notifications = await client.get_notifications()
    if not notifications:
        print(f"{label}: Unable to fetch notifications for ID {notif_id}")
        return
    record_map = notifications.get("recordMap", {})
    notif_data = record_map.get("notification", {}).get(notif_id, {}).get("value", {})
    if notif_data:
        # Print only important fields
        status = {k: notif_data[k] for k in ("read", "visited", "archived_at") if k in notif_data}
        print(f"{label} ({notif_id}): {json.dumps(status, indent=2)}")
    else:
        print(f"{label} ({notif_id}): (not found or no data)")


async def test_all_notification_methods() -> None:
    settings = Settings()  # type: ignore[call-arg]
    client = NotionClient(settings)
    notifications = await client.get_notifications()
    if notifications is None:
        print("No notifications found.")
        return
    print("Raw notifications:\n", json.dumps(notifications, indent=2))
    notification_ids = notifications.get("notificationIds", [])
    if not notification_ids:
        print("No notification IDs found.")
        return

    for notif_id in notification_ids:
        print("\n--- Working on notification:", notif_id, "---\n")

        # Read
        client.export_notification_id = notif_id
        result = await client.mark_notifications_as_read()
        print(f"Marked as read: {result}")
        await print_notification_status(client, notif_id, "After read")

        # Unread
        client.export_notification_id = notif_id
        result = await client.mark_notifications_as_unread()
        print(f"Marked as unread: {result}")
        await print_notification_status(client, notif_id, "After unread")

        # Read again (for toggling)
        client.export_notification_id = notif_id
        result = await client.mark_notifications_as_read()
        print(f"Marked as read (again): {result}")
        await print_notification_status(client, notif_id, "After second read")

        # Archive
        client.export_notification_id = notif_id
        result = await client.mark_notification_as_archived()
        print(f"Archived: {result}")
        await print_notification_status(client, notif_id, "After archive")

        # Unarchive
        client.export_notification_id = notif_id
        result = await client.mark_notification_as_unarchived()
        print(f"Unarchived: {result}")
        await print_notification_status(client, notif_id, "After unarchive")


if __name__ == "__main__":
    asyncio.run(test_all_notification_methods())
