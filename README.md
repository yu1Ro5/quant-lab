# quant-lab

This script fetches the current USD/JPY quote from the GMO Coin Foreign Exchange FX Public API, prints it, stores it in CSV, and optionally sends a Slack notification.

The public ticker endpoint is `GET https://forex-api.coin.z.com/public/v1/ticker` and does not require an API key or secret. The script selects `USD_JPY` by its symbol rather than relying on its position in the returned list. Market state is fetched separately from `GET https://forex-api.coin.z.com/public/v1/status`, because ticker records contain prices but no `OPEN`/`CLOSE` field.

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
| `rate` | Mid price, calculated as `(bid + ask) / 2` |
| `bid` | Current sell price |
| `ask` | Current buy price |
| `spread` | Bid/ask spread, calculated as `ask - bid` |
| `source_timestamp` | UTC timestamp returned by the GMO Coin API |
| `market_status` | Market state (`OPEN` or `CLOSE`) |

Prices and derived values are calculated with decimal arithmetic. `rate_date` is the API timestamp's date after conversion to Japan Standard Time, while `fetched_at` remains the time the script ran in UTC. Existing three-column CSV files are upgraded automatically when a new row is saved, and their existing data is retained.

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
