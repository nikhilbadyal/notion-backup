# Notion Backup - Modular Version

A powerful, modular Python tool to automatically back up your Notion workspace with pluggable storage backends and notification systems.

## ✨ Features

- 🚀 **Export entire Notion workspace** - Complete backup of all your data
- 📦 **Pluggable storage backends** - Local, rclone (cloud storage), with more coming
- 📧 **Rich notifications** - Apprise integration for 70+ notification services
- 🔧 **Highly configurable** - Customize every aspect via environment variables
- 📝 **Detailed logging** - Comprehensive logging for debugging and monitoring
- 🔒 **Secure** - Token-based authentication with Notion
- ⚡ **Async/await support** - Fast, concurrent operations
- 🧹 **Automatic cleanup** - Keep only the most recent backups
- 📊 **Multiple export formats** - Markdown or HTML output
- 🔄 **Retry logic** - Robust error handling with exponential backoff
- 📬 **Smart notification management** - Automatically mark export notifications as read
- 🔄 **Export Recovery** - Optional Redis integration to recover from rare Notion notification failures

## 🏗️ Architecture

The tool is built with a modular, pluggable architecture:

```
src/notion_backup/
├── core/           # Core backup logic
│   ├── client.py   # Notion API client
│   └── backup.py   # Main backup orchestrator
├── storage/        # Storage backends
│   ├── local.py    # Local file storage
│   └── rclone.py   # Rclone cloud storage
├── notifiers/      # Notification backends
│   └── apprise.py  # Apprise notifications
├── config/         # Configuration management
└── utils/          # Utility functions
    └── redis_client.py # Optional Redis client for export recovery
```

## 🚀 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/nikhilbadyal/notion.git
cd notion-backup

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

### 3. Get Notion Credentials

1. Login to [Notion](https://www.notion.so/login)
2. Open browser developer console → Network tab
3. Use "Quick Find" and search for something
4. Find the `search` request and copy:
   - `spaceId` → `NOTION_SPACE_ID`
   - `token_v2` cookie → `NOTION_TOKEN_V2`
   - `file_token` cookie → `NOTION_FILE_TOKEN`

### 4. Basic Configuration

Edit your `.env` file with your credentials:

```bash
# Required
NOTION_SPACE_ID=your_actual_space_id_here
NOTION_TOKEN_V2=your_actual_token_v2_here
NOTION_FILE_TOKEN=your_file_token

# Optional - defaults shown
STORAGE_BACKEND=local
EXPORT_TYPE=markdown
LOCAL_PATH=./downloads
```

### 5. Run Backup

```bash
# Simple backup
python main.py backup

# With debug logging
python main.py --debug backup

# List available backups
python main.py list

# Cleanup old backups (keep 5 most recent)
python main.py cleanup --keep 5
```

## 🐳 Docker

This project is fully containerized, allowing you to run the backup tool in a consistent and isolated environment.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

### Running with Docker Compose

1. **Configure Environment:**
    Create a `.env` file by copying the `.env.example` and filling in your Notion credentials and other settings.

2. **Build and Run:**
    Use `docker-compose` to build the image and run the backup service.

    ```bash
    # Build the Docker image
    docker-compose build

    # Run a one-off backup
    docker-compose run --rm notion-backup
    ```

### Scheduling Backups

To run backups on a schedule, you can use a standard cron job on your host machine to execute the `docker-compose run` command.

**Example Cron Job (daily at 2 AM):**

```bash
0 2 * * * cd /path/to/notion-backup && /usr/local/bin/docker-compose run --rm notion-backup
```

## 📦 Storage Backends

### Local Storage

Store backups on local filesystem:

```bash
STORAGE_BACKEND=local
LOCAL_PATH=./downloads
MAX_BACKUPS=10
```

### Rclone Storage

Store backups on any cloud provider supported by rclone:

```bash
STORAGE_BACKEND=rclone
RCLONE_REMOTE=mycloud
RCLONE_PATH=notion-backups
RCLONE_CONFIG_PATH=/path/to/rclone.conf
KEEP_LOCAL_BACKUP=true
```

## 📧 Notifications

Get notified about backup status via 70+ services using [Apprise](https://github.com/caronc/apprise):

```bash
ENABLE_NOTIFICATIONS=true
NOTIFICATION_LEVEL=all
APPRISE_URLS=discord://webhook_id/webhook_token,mailto://user:pass@smtp.gmail.com?to=you@gmail.com
```

**Popular notification services:**

- **Discord:** `discord://webhook_id/webhook_token`
- **Slack:** `slack://TokenA/TokenB/TokenC/Channel`
- **Email:** `mailto://user:pass@domain.com?to=recipient@domain.com`
- **Telegram:** `tgram://bottoken/ChatID`
- **Microsoft Teams:** `msteams://TokenA/TokenB/TokenC/`
- **PushBullet:** `pbul://accesstoken`

[See full list of supported services](https://github.com/caronc/apprise/wiki)

## ⚙️ Configuration Reference

### Required Settings

| Variable            | Description                 |
|---------------------|-----------------------------|
| `NOTION_SPACE_ID`   | Your Notion workspace ID    |
| `NOTION_TOKEN_V2`   | Notion authentication token |
| `NOTION_FILE_TOKEN` | Notion file download token  |

### Export Settings

| Variable                  | Default    | Options            | Description          |
|---------------------------|------------|--------------------|----------------------|
| `EXPORT_TYPE`             | `markdown` | `markdown`, `html` | Export format        |
| `FLATTEN_EXPORT_FILETREE` | `false`    | `true`, `false`    | Flatten nested pages |
| `EXPORT_COMMENTS`         | `true`     | `true`, `false`    | Include comments     |
| `TIME_ZONE`               | `UTC`      | Any valid TZ name  | Timezone for export  |

### Storage Settings

| Variable          | Default       | Description               |
|-------------------|---------------|---------------------------|
| `STORAGE_BACKEND` | `local`       | `local`, `rclone`         |
| `LOCAL_PATH`      | `./downloads` | Local storage directory   |
| `MAX_BACKUPS`     | `None`        | Max local backups to keep |

### Rclone Settings

| Variable             | Default          | Description                  |
|----------------------|------------------|------------------------------|
| `RCLONE_REMOTE`      | `None`           | Rclone remote name           |
| `RCLONE_PATH`        | `notion-backups` | Path on remote               |
| `RCLONE_CONFIG_PATH` | `None`           | Rclone config file path      |
| `KEEP_LOCAL_BACKUP`  | `true`           | Keep local copy after upload |

### Notification Settings

| Variable               | Default         | Description                       |
|------------------------|-----------------|-----------------------------------|
| `ENABLE_NOTIFICATIONS` | `false`         | Enable notifications              |
| `NOTIFICATION_LEVEL`   | `all`           | `success`, `error`, `all`, `none` |
| `APPRISE_URLS`         | `""`            | Comma-separated notification URLs |
| `NOTIFICATION_TITLE`   | `Notion Backup` | Notification title prefix         |

### Advanced Settings

| Variable                     | Default | Description                                      |
|------------------------------|---------|--------------------------------------------------|
| `MAX_RETRIES`                | `3`     | Max retry attempts                               |
| `RETRY_DELAY`                | `5`     | Delay between retries (seconds)                  |
| `DOWNLOAD_TIMEOUT`           | `300`   | Download timeout (seconds)                       |
| `MAX_EXPORT_WAIT_TIME`       | `3600`  | Max time to wait for export completion (seconds) |
| `EXPORT_POLL_INTERVAL`       | `10`    | Interval between export checks (seconds)         |
| `MAX_RETRY_DELAY`            | `300`   | Maximum delay between retries (seconds)          |
| `MARK_NOTIFICATIONS_AS_READ` | `true`  | Mark export notifications as read after download |
| `ARCHIVE_NOTIFICATION`       | `false` | Archive export notification after upload         |

### Export Recovery Settings (Optional)

This feature uses Redis to recover from rare cases where a Notion export succeeds but the completion notification isn't received. When enabled, failed notifications are queued in Redis and processed on subsequent backup runs.

| Variable              | Default    | Description                                                   |
|-----------------------|------------|---------------------------------------------------------------|
| `REDIS_HOST`          | None       | Redis server hostname (enables feature)                       |
| `REDIS_PORT`          | 6379       | Redis port                                                    |
| `REDIS_DB`            | 0          | Redis database number (0-15)                                  |
| `REDIS_USERNAME`      | None       | Redis username (for ACL, Redis 6+)                            |
| `REDIS_PASSWORD`      | None       | Redis password (optional)                                     |
| `REDIS_SSL`           | false      | Enable SSL/TLS for Redis connection                           |
| `REDIS_SSL_CA_CERTS`  | None       | Path to Redis CA certificate file                             |
| `REDIS_SSL_CERT_REQS` | "required" | SSL certificate requirements ('required', 'optional', 'none') |

**Note:** Redis is completely optional. If `REDIS_HOST` is not set, the feature is disabled and backups proceed normally. Install Redis via your package manager or Docker for production use. For TLS-enabled Redis servers (common in cloud providers), set `REDIS_SSL=true` and provide certificate details if required.

## 🔧 Command Line Usage

```bash
# Run backup (default command)
python main.py
python main.py backup

# List available backups
python main.py list

# Clean up old backups
python main.py cleanup --keep 5

# Test configuration
python main.py test

# Enable debug logging
python main.py --debug backup
```

## 🔌 Extending the System

### Adding New Storage Backends

1. Create a new class inheriting from `AbstractStorage`
2. Implement required methods: `store`, `list_backups`, `cleanup_old_backups`, `test_connection`
3. Register in `BackupManager._create_storage_backend()`

### Adding New Notifiers

1. Create a new class inheriting from `AbstractNotifier`
2. Implement required methods: `send_notification`, `test_connection`
3. Register in `BackupManager._create_notifier()`

## 📋 Example Configurations

### Personal Local Backup

```bash
# .env
NOTION_SPACE_ID=your_space_id
NOTION_TOKEN_V2=your_token
NOTION_FILE_TOKEN=your_file_token

STORAGE_BACKEND=local
LOCAL_PATH=./backups
MAX_BACKUPS=7

ENABLE_NOTIFICATIONS=true
APPRISE_URLS=mailto://user:pass@gmail.com?to=you@gmail.com
MARK_NOTIFICATIONS_AS_READ=true
```

### Cloud Backup with Discord Notifications

```bash
# .env
NOTION_SPACE_ID=your_space_id
NOTION_TOKEN_V2=your_token
NOTION_FILE_TOKEN=your_file_token

STORAGE_BACKEND=rclone
RCLONE_REMOTE=gdrive
RCLONE_PATH=backups/notion
KEEP_LOCAL_BACKUP=false

ENABLE_NOTIFICATIONS=true
APPRISE_URLS=discord://webhook_id/webhook_token
NOTIFICATION_LEVEL=all
```

### Enterprise Setup

```bash
# .env
NOTION_SPACE_ID=your_space_id
NOTION_TOKEN_V2=your_token
NOTION_FILE_TOKEN=your_file_token

STORAGE_BACKEND=rclone
RCLONE_REMOTE=s3backup
RCLONE_PATH=company-notion-backups
RCLONE_ADDITIONAL_ARGS=--transfers=8,--checkers=16

ENABLE_NOTIFICATIONS=true
APPRISE_URLS=slack://TokenA/TokenB/TokenC/general,mailto://backup@company.com?to=it@company.com
NOTIFICATION_LEVEL=all

MAX_RETRIES=5
RETRY_DELAY=10
```

## 🤖 Automation

### Cron Job (Linux/macOS)

```bash
# Daily backup at 2 AM
0 2 * * * cd /path/to/notion-backup && python main.py backup

# Weekly cleanup (keep 30 backups)
0 3 * * 0 cd /path/to/notion-backup && python main.py cleanup --keep 30
```

### Windows Task Scheduler

Create a task that runs:

```
Program: python
Arguments: /path/to/notion-backup/main.py backup
Start in: /path/to/notion-backup
```

## 🐛 Troubleshooting

### Common Issues

#### "Configuration Error: Field required"

- Ensure all required environment variables are set
- Check `.env` file syntax

#### "Storage connection failed"

- For local: Check directory permissions
- For rclone: Test rclone config with `rclone lsd remote:`

#### "Export task failed"

- Token may have expired - get new tokens from browser
- Check network connectivity
- Verify space ID is correct

#### "Notification failed"

- Test notification URLs independently
- Check URL format in Apprise documentation

### Debug Mode

Enable debug logging for detailed troubleshooting:

```bash
python main.py --debug backup
```

### Testing Configuration

```bash
# Test all connections
python main.py test

# Test specific components
python -c "from src.notion_backup.config import Settings; s=Settings(); print('Config loaded successfully')"
```

## 🔒 Security Notes

- Store `.env` file securely, never commit to version control
- `NOTION_TOKEN_V2` provides full workspace access
- Tokens expire after ~1 year or on logout
- Use environment variables in production instead of `.env` files
- Regularly rotate tokens and review access

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Ensure all tests pass
5. Submit a pull request
