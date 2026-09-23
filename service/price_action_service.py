from datetime import date, timedelta

import pandas as pd
import requests

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

PRICE_HISTORY_COLUMNS = [
    "date_time", "orderbook_id", "isin", "symbol", "company",
    "bid", "ask", "open", "high", "low", "close", "average",
    "total_volume", "turnover", "trades",
]

DEFAULT_HISTORY_DAYS = 200


def fetch_price_history_from_nasdaq(orderbook_id, from_date=None, to_date=None):
    to_date = to_date or date.today().isoformat()
    from_date = from_date or (
        date.fromisoformat(to_date) - timedelta(days=DEFAULT_HISTORY_DAYS)
    ).isoformat()

    price_history_url = (
        f"https://api.nasdaq.com/api/nordic/instruments/{orderbook_id}/chart/download"
        f"?assetClass=SHARES&fromDate={from_date}&toDate={to_date}"
    )
    #print(f"price_history_url: {price_history_url}")

    response = requests.get(price_history_url, headers=headers, timeout=30)
    payload = response.json()

    data = payload.get("data") or {}
    chart_data = data.get("chartData") or {}
    charts = data.get("charts") or {}
    rows = charts.get("rows") or []

    valid_rows = [r for r in rows if isinstance(r, dict) and r.get("dateTime")]
    if not chart_data.get("orderbookId") or not valid_rows:
        return pd.DataFrame(columns=PRICE_HISTORY_COLUMNS)

    df = pd.DataFrame(valid_rows)
    df = df.rename(columns={"dateTime": "date_time", "totalVolume": "total_volume"})

    df["orderbook_id"] = str(chart_data.get("orderbookId") or "")
    df["isin"] = chart_data.get("isin", "")
    df["symbol"] = chart_data.get("symbol", "")
    df["company"] = chart_data.get("company", "")

    for column in PRICE_HISTORY_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df[PRICE_HISTORY_COLUMNS].sort_values("date_time").reset_index(drop=True)
    return df
