# quant-lab

This script fetches the current USD/JPY exchange rate and prints it.

## Slack notification

Set the Slack bot token and target channel before running the script:

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_CHANNEL="#alerts"
python main.py
```

If either environment variable is not set, the script prints the exchange rate and skips the Slack notification.
