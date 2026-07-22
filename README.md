# quant-lab

This script fetches the current USD/JPY exchange rate, prints it, stores it in CSV, and optionally sends a Slack notification.

## Usage

Run the script:

```bash
python main.py
```

The script fetches the latest USD/JPY rate and appends it to `data/usd_jpy.csv`. The `data` directory is created automatically when it does not exist.

If a row for the same `rate_date` already exists, the script skips writing a duplicate row.

## CSV history

The CSV file is stored at `data/usd_jpy.csv` with the following columns:

| Column | Description |
| --- | --- |
| `fetched_at` | UTC timestamp when the rate was fetched |
| `rate_date` | Rate date returned by the exchange-rate API |
| `rate` | USD/JPY exchange rate |

## Slack notification

Set the Slack bot token and target channel before running the script:

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token"
export SLACK_CHANNEL="#alerts"
python main.py
```

If either environment variable is not set, the script prints the exchange rate and skips the Slack notification.

## Tests and CI

Run the test suite locally with:

```bash
uv run python -m unittest discover -v
```

GitHub Actions runs the same test suite for pull requests targeting `main`, pushes to `main`, and manual workflow runs. The CI workflow uses Python 3.12 and installs the dependencies locked in `uv.lock`.
