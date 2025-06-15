# Environment Variable Formats

This document explains the supported formats for list-type environment variables in Notion Backup.

## Overview

The Notion Backup tool uses a **simple comma-separated format** for all list-type environment variables. This format works consistently across all deployment environments (local development, GitHub Actions, Docker, Kubernetes, etc.).

## Supported Variables

### `RCLONE_ADDITIONAL_ARGS`
Additional command-line arguments to pass to rclone.

### `APPRISE_URLS` 
Notification service URLs for Apprise.

## Supported Format

### Comma-Separated Values Only

```bash
# Single argument
RCLONE_ADDITIONAL_ARGS=--verbose

# Multiple arguments
RCLONE_ADDITIONAL_ARGS=--verbose,--transfers=8,--checkers=16

# Single notification URL
APPRISE_URLS=tgram://bot_token/chat_id

# Multiple notification URLs
APPRISE_URLS=discord://webhook_id/token,tgram://bot_token/chat_id,slack://tokenA/tokenB/tokenC/channel

# Empty values (uses defaults)
RCLONE_ADDITIONAL_ARGS=
APPRISE_URLS=
```

## Rejected Formats

### ❌ JSON Array Format (Not Supported)

```bash
# These will cause the program to exit with an error:
RCLONE_ADDITIONAL_ARGS=["--verbose", "--transfers=8"]
APPRISE_URLS=["discord://webhook", "tgram://token/chat"]

# Error message:
# "JSON array format is not supported. Use comma-separated format: value1,value2,value3"
```

## Automatic Cleaning

The parser handles basic formatting issues:

### ✅ Whitespace and Quote Removal
```bash
# These inputs:
RCLONE_ADDITIONAL_ARGS=" --verbose , --transfers=8 "
APPRISE_URLS=' "tgram://token/chat" , "discord://webhook" '

# Are cleaned to:
RCLONE_ADDITIONAL_ARGS=--verbose,--transfers=8
APPRISE_URLS=tgram://token/chat,discord://webhook
```

## Examples by Environment

### Local Development (.env file)
```bash
RCLONE_ADDITIONAL_ARGS=--verbose,--transfers=8
APPRISE_URLS=tgram://your_bot_token/your_chat_id
```

### GitHub Actions (ENVS secret)
```bash
RCLONE_ADDITIONAL_ARGS=["--verbose", "--transfers=8"]
APPRISE_URLS=["tgram://your_bot_token/your_chat_id"]
```

### Docker Environment
```bash
# docker-compose.yml
environment:
  - RCLONE_ADDITIONAL_ARGS=--verbose,--transfers=8
  - APPRISE_URLS=discord://webhook/token
```

### Kubernetes ConfigMap
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: notion-backup-config
data:
  RCLONE_ADDITIONAL_ARGS: '["--verbose", "--transfers=8"]'
  APPRISE_URLS: '["discord://webhook/token"]'
```

## Security Features

### Automatic Secret Masking

Sensitive parts of configuration are automatically masked in logs:

**Raw URLs (never logged):**
```
tgram://7836881242:AAEsnXet-SPqzI9RzSwIGp2q6h_Eenjngvg/-1002351247537
```

**Masked URLs (safe for logs):**
```
tgram://****/-1002351247537
```

### Supported Masking Patterns

- **Telegram**: `tgram://token/chat` → `tgram://****/chat`
- **Discord**: `discord://webhook_id/token` → `discord://webhook_id/****`
- **Slack**: `slack://tokenA/tokenB/tokenC/channel` → `slack://****`
- **Email**: `mailto://user:pass@domain` → `mailto://user:****@domain`

## Troubleshooting

### Common Issues

**1. Rclone command fails with "too many arguments"**
```
# ❌ Wrong - includes literal brackets
Command about needs 1 arguments maximum: you provided 2 non flag arguments: ["r2:" "[--verbose]"]

# ✅ Fixed - automatic bracket removal
RCLONE_ADDITIONAL_ARGS: ['--verbose']
```

**2. Apprise fails to parse URL**
```
# ❌ Wrong - extra bracket
tgram://token/chat/?topic=123]

# ✅ Fixed - automatic cleanup
tgram://token/chat/?topic=123
```

**3. Empty configuration**
```bash
# These are all equivalent and valid:
RCLONE_ADDITIONAL_ARGS=
RCLONE_ADDITIONAL_ARGS=[]
RCLONE_ADDITIONAL_ARGS=""
# (or omit the variable entirely)
```

### Validation

Test your configuration with:
```bash
python main.py test
```

This will validate all settings and show any parsing issues. 
