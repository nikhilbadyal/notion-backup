# GitHub Actions Setup Guide

This guide explains how to set up automated Notion backups using GitHub Actions with a single, highly configurable workflow.

## 🚀 Quick Setup

### 1. Create the ENVS Secret

1. Go to your GitHub repository
2. Click on **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Name: `ENVS`
5. Value: Base64 encode the content of your entire `.env` file and paste the encoded string here.

   **How to Base64 encode your `.env` file:**
   ```bash
   base64 -i .env
   # On Windows (PowerShell):
   # [System.Convert]::ToBase64String([System.IO.File]::ReadAllBytes(".env"))
   ```

Example ENVS secret content (after base64 encoding):
```
Tk9USU9OX1NQQUNFX0lEPXlvdXJfc3BhY2VfaWRfaGVyZQpOT1RJT05fVE9LRU5fVjI9eW91cl90b2tlbl92Ml9oZXJlCk5PVElPTl9GSUxFX1RPS0VOPXlvdXJfZmlsZV90b2tlbl9oZXJlCk5PVElPTl9FWFBPUlRfVFlQRT1tYXJrZG93bgpOT1RJT05fRkxBVFRFTl9FWFBPUlRfRklMRVRSRUU9ZmFsc2UKTk9USU9OX0VYUE9SVF9DT01NRU5UUz10cnVlClNUT1JBR0VfQkFDS0VORD1yY2xvbmUKUkNMT05FX1JFTU9URT1yMgpSQ0xPTkVfUEFUSD1ub3Rpb24KUkNMT05FX0NPTkZJR19QQVRIPX4vLmNvbmZpZy9yY2xvbmUvcmNsb25lLmNvbmYKUkNMT05FX0FERElUSU9OQUxfQVJHUz0tLXZlcmJvc2UKRU5BQkxFX05PVElGSUNBVElPTlM9dHJ1ZQpBUFJSSVNFX1VSTFM9dGdyYW06Ly95b3VyX2JvdF90b2tlbi95b3VyX2NoYXRfaWQKTUFYX0JBQ0tVUFM9MTA=
```

**⚠️ Important: List Variable Format**

For variables that accept multiple values (like `RCLONE_ADDITIONAL_ARGS` and `APPRISE_URLS`), use **comma-separated format only**:

```
# Single values
RCLONE_ADDITIONAL_ARGS=--verbose
APPRISE_URLS=tgram://your_bot_token/your_chat_id

# Multiple values
RCLONE_ADDITIONAL_ARGS=--verbose,--transfers=8,--checkers=16
APPRISE_URLS=discord://webhook,tgram://token/chat,slack://tokenA/tokenB/tokenC/channel
```

**❌ JSON array format is NOT supported and will cause the program to exit with an error.**

### 2. Test Your Setup

1. Go to **Actions** tab in your repository
2. Select "Notion Backup"
3. Click **Run workflow**
4. ✅ **Enable dry-run mode** for testing
5. Click **Run workflow**

## 🔧 Workflow Modes

The single workflow (`.github/workflows/backup.yml`) can operate in two modes:

### Simple Mode (Automatic Scheduled Runs)
- ✅ Runs automatically on schedule using default settings
- ✅ No manual intervention required
- ✅ Modify defaults in the workflow file's `env` section

**To customize simple mode:**
1. Edit `.github/workflows/backup.yml`
2. Modify the values in the `env` section:
   ```yaml
   env:
     DEFAULT_TIMEOUT: 30           # Job timeout in minutes
     DEFAULT_UPLOAD_LOGS: true     # Upload logs as artifacts
     DEFAULT_DEBUG: false          # Enable debug logging
     DEFAULT_DRY_RUN: false        # Use dry-run mode (for testing)
   ```

### Advanced Mode (Manual Trigger)
- ✅ Full control via manual trigger options
- ✅ Override any setting for specific runs
- ✅ Perfect for testing and debugging

**Manual trigger options:**
- **Dry Run**: Skip Notion API, use dummy file
- **Debug**: Enable verbose logging
- **Upload Logs**: Save logs as downloadable artifacts
- **Timeout**: Custom timeout in minutes

## ⏰ Schedule Configuration

### Default Schedule
- Runs daily at 2 AM UTC
- Modify the cron expression in the workflow file

### Common Schedule Examples
Edit the `schedule` section in `.github/workflows/backup.yml`:

```yaml
schedule:
  - cron: '0 2 * * *'     # Daily at 2 AM UTC
  # - cron: '0 */6 * * *'   # Every 6 hours
  # - cron: '0 2 * * 1'     # Weekly on Monday at 2 AM
  # - cron: '0 2 1 * *'     # Monthly on the 1st at 2 AM
  # - cron: '0 2 * * 0'     # Weekly on Sunday at 2 AM
```

## 🎯 Usage Examples

### For Simple Set-and-Forget Operation
1. Set up the `ENVS` secret
2. Optionally modify defaults in the workflow file
3. Let it run automatically on schedule

### For Testing and Development
1. Use manual trigger with dry-run mode enabled
2. Enable debug logging for troubleshooting
3. Download log artifacts for detailed analysis

### For Different Environments
1. Create multiple secrets (`ENVS_PROD`, `ENVS_TEST`)
2. Modify the workflow to use different secrets
3. Run different configurations as needed

## 🔍 Monitoring and Debugging

### Viewing Logs
- **In GitHub**: Check the workflow run logs in the Actions tab
- **Download**: Log artifacts are available for 30 days (if enabled)
- **Real-time**: Watch the workflow run in real-time

### Log Artifacts
When `upload_logs` is enabled (default), you can:
1. Go to the completed workflow run
2. Scroll down to "Artifacts" section
3. Download `backup-logs-{run_number}.zip`
4. Extract and view timestamped log files

### Configuration Display
Each run shows the current configuration:
```
🔧 Backup Configuration:
├── Dry Run: false
├── Debug: false
├── Upload Logs: true
├── Timeout: 30 minutes
├── Trigger: schedule
└── Runner: Linux
```

## 🛠️ Customization

### Change Default Behavior
Edit the `env` section in `.github/workflows/backup.yml`:
```yaml
env:
  DEFAULT_TIMEOUT: 45           # Increase timeout
  DEFAULT_DEBUG: true           # Enable debug by default
  DEFAULT_UPLOAD_LOGS: false    # Disable log uploads
  DEFAULT_DRY_RUN: true         # Test mode by default
```

### Add Custom Steps
You can add custom steps to the workflow:
```yaml
- name: Custom notification
  if: success()
  run: |
    echo "Backup completed successfully!"
    # Add your custom logic here
```

### Multiple Schedules
Add multiple schedule triggers:
```yaml
schedule:
  - cron: '0 2 * * *'    # Daily at 2 AM
  - cron: '0 14 * * 0'   # Weekly on Sunday at 2 PM
```

## 🔒 Security Best Practices

1. **Never commit `.env` files** to your repository
2. **Use repository secrets** for sensitive data
3. **Regularly rotate** your Notion tokens
4. **Monitor workflow runs** for any failures
5. **Use dry-run mode** for testing changes
6. **Review workflow logs** regularly
7. **Limit secret access** to necessary collaborators

## 🚨 Troubleshooting

### Common Issues

1. **"ENVS secret is not set"**
   - Verify the secret exists in repository settings
   - Check the secret name is exactly `ENVS` (case-sensitive)

2. **"Required variable not found"**
   - Ensure all required variables are in your ENVS secret
   - Required: `NOTION_SPACE_ID`, `NOTION_TOKEN_V2`, `NOTION_FILE_TOKEN`

3. **Backup fails with rate limiting**
   - Use dry-run mode for testing
   - Space out backup attempts
   - Check Notion API status

4. **Storage connection fails**
   - Verify rclone configuration in ENVS secret
   - Test locally first with `python main.py --dry-run`
   - Check storage service status

5. **Workflow times out**
   - Increase timeout in manual trigger or defaults
   - Check for network issues
   - Monitor backup file sizes

### Debug Steps
1. **Enable dry-run mode** to test without API calls
2. **Enable debug logging** for verbose output
3. **Download log artifacts** for detailed analysis
4. **Test locally** with the same configuration
5. **Check GitHub Actions status** page

## 📊 Workflow Features Summary

| Feature                | Available | Configurable        |
|------------------------|-----------|---------------------|
| Scheduled runs         | ✅         | ✅ (cron expression) |
| Manual triggers        | ✅         | ✅ (5 options)       |
| Dry-run mode           | ✅         | ✅                   |
| Debug logging          | ✅         | ✅                   |
| Log artifacts          | ✅         | ✅                   |
| Timeout control        | ✅         | ✅                   |
| Environment validation | ✅         | ❌                   |
| Automatic cleanup      | ✅         | ❌                   |
| Configuration display  | ✅         | ❌                   |

## 💡 Pro Tips

1. **Start with dry-run** to test your configuration
2. **Use debug mode** when setting up for the first time
3. **Download logs** for troubleshooting complex issues
4. **Set reasonable timeouts** based on your backup size
5. **Monitor the Actions tab** for run history
6. **Use manual triggers** for testing configuration changes
7. **Keep your ENVS secret updated** when changing local config

## 🆘 Support

If you encounter issues:
1. Check the workflow logs in the Actions tab
2. Download and review log artifacts
3. Test locally with `python main.py --dry-run`
4. Verify your ENVS secret contains all required variables
5. Check that your storage configuration is working
6. Review the troubleshooting section above 
