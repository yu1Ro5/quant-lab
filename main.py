import requests


def get_usd_jpy() -> tuple[float, str]:
    url = "https://api.frankfurter.dev/v1/latest?from=USD&to=JPY"

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    data = response.json()

    return data["rates"]["JPY"], data["date"]


if __name__ == "__main__":
    rate, date = get_usd_jpy()

    print("=== USD/JPY ===")
    print(f"Rate : {rate}")
    print(f"Date : {date}")
