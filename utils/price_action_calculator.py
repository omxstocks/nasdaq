from collections import deque

from .calculate_probability import calculate_buy_conviction
from .market_analysis import generate_market_semantics
from .technical_indicators import calculate_atr, calculate_rsi, calculate_rsi_slope

EMA_PERIODS = [2, 5, 9, 14, 21, 50, 55, 100, 200]
EMA_COLUMNS = {period: f"ema_{period}_pct" for period in EMA_PERIODS}
VOLUME_AVG_WINDOW = 21

RSI_ATR_PERIODS = [9, 13, 21]
RSI_COLUMNS = {period: f"rsi_{period}" for period in RSI_ATR_PERIODS}
ATR_COLUMNS = {period: f"atr_{period}" for period in RSI_ATR_PERIODS}
RSI_SLOPE_COLUMNS = {period: f"rsi_slope_{period}" for period in RSI_ATR_PERIODS}

# rsi_slope_9/rsi_9 feed market-semantics and buy-conviction generation, matching the
# fastest EMA (ema_9) used there
MARKET_SEMANTICS_RSI_SLOPE_PERIOD = 9
MARKET_SEMANTICS_COLUMNS = {
    "macro_context": "market_macro_context",
    "tactical_state": "market_tactical_state",
    "volume_description": "market_volume_description",
    "rsi_slope_description": "market_rsi_slope_description",
    "momentum_confluence": "market_momentum_confluence",
    "human_readable": "market_human_readable",
}

BUY_CONVICTION_COLUMNS = {
    "probability_percent": "buy_conviction_probability",
    "reason": "buy_conviction_reason",
}


def _parse_price(value):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _apply_ema_update(ema_state, close_price):
    for period in EMA_PERIODS:
        k = 2 / (period + 1)
        ema_state[period] = close_price if ema_state[period] is None else close_price * k + ema_state[period] * (1 - k)


def _seed_state_from_history(history):
    ema_state = {period: None for period in EMA_PERIODS}
    volume_window = deque(maxlen=VOLUME_AVG_WINDOW)
    last_close = None
    combined_closes, combined_highs, combined_lows, combined_targets = [], [], [], []

    for close_raw, volume_raw, high_raw, low_raw in history:
        close_price = _parse_price(close_raw)
        volume = _parse_price(volume_raw)
        high_price = _parse_price(high_raw)
        low_price = _parse_price(low_raw)
        if close_price is not None:
            _apply_ema_update(ema_state, close_price)
            last_close = close_price
        if volume is not None:
            volume_window.append(volume)
        if close_price is not None and high_price is not None and low_price is not None:
            combined_closes.append(close_price)
            combined_highs.append(high_price)
            combined_lows.append(low_price)
            combined_targets.append(None)

    return ema_state, volume_window, last_close, combined_closes, combined_highs, combined_lows, combined_targets


def _compute_batch_indicators(valid_rows, combined_closes, combined_highs, combined_lows, combined_targets):
    for row in valid_rows:
        close_price = _parse_price(row.get("close"))
        high_price = _parse_price(row.get("high"))
        low_price = _parse_price(row.get("low"))
        if close_price is not None and high_price is not None and low_price is not None:
            combined_closes.append(close_price)
            combined_highs.append(high_price)
            combined_lows.append(low_price)
            combined_targets.append(row)

    for period in RSI_ATR_PERIODS:
        rsi_series = calculate_rsi(combined_closes, period)
        atr_series = calculate_atr(combined_highs, combined_lows, combined_closes, period)
        rsi_slope_series = calculate_rsi_slope(rsi_series, period)
        for target, rsi_value, atr_value, rsi_slope_value in zip(combined_targets, rsi_series, atr_series, rsi_slope_series):
            if target is None:
                continue
            target[RSI_COLUMNS[period]] = f"{rsi_value:.2f}" if rsi_value is not None else ""
            target[ATR_COLUMNS[period]] = f"{atr_value:.4f}" if atr_value is not None else ""
            target[RSI_SLOPE_COLUMNS[period]] = f"{rsi_slope_value:.4f}" if rsi_slope_value is not None else ""
            if period == MARKET_SEMANTICS_RSI_SLOPE_PERIOD:
                target["_raw_rsi_slope"] = rsi_slope_value
                target["_raw_rsi"] = rsi_value


def enrich_price_action_rows(valid_rows, history):
    """Mutates and returns valid_rows, adding every derived price_action column.

    valid_rows: new rows (dicts) for one instrument, sorted ascending by date_time.
    history: (close, total_volume, high, low) tuples preceding valid_rows, sorted ascending,
             used to seed EMA/RSI/ATR/volume-average state so indicators are continuous
             across incremental fetches instead of resetting on each call.
    """
    ema_state, volume_window, last_close, combined_closes, combined_highs, combined_lows, combined_targets = \
        _seed_state_from_history(history)

    _compute_batch_indicators(valid_rows, combined_closes, combined_highs, combined_lows, combined_targets)

    for row in valid_rows:
        close_price = _parse_price(row.get("close"))
        open_price = _parse_price(row.get("open"))
        volume = _parse_price(row.get("total_volume"))

        net_change = ""
        percentage_change = ""
        if open_price is not None and close_price is not None:
            change = open_price - close_price
            net_change = f"{change:+.2f}"
            if open_price != 0:
                percentage_change = f"{(change / open_price) * 100:+.2f}%"

        previous_close = f"{last_close:.4f}" if last_close is not None else ""
        prev_close_pct_diff = ""
        if close_price is not None and last_close:
            prev_close_pct_diff = f"{((close_price - last_close) / last_close) * 100:+.2f}%"

        ema_values = {}
        if close_price is not None:
            _apply_ema_update(ema_state, close_price)
            for period in EMA_PERIODS:
                ema = ema_state[period]
                ema_values[period] = f"{((close_price - ema) / ema) * 100:+.2f}%" if ema else ""
        else:
            for period in EMA_PERIODS:
                ema_values[period] = ""

        volume_avg_ratio = ""
        raw_volume_ratio = None
        if volume is not None:
            volume_window.append(volume)
            avg_volume = sum(volume_window) / len(volume_window)
            if avg_volume:
                raw_volume_ratio = volume / avg_volume
                volume_avg_ratio = f"{raw_volume_ratio:.4f}"

        market_semantics = {}
        buy_conviction = {}
        if close_price is not None and raw_volume_ratio is not None:
            market_semantics = generate_market_semantics(
                close_price,
                ema_state[9],
                ema_state[21],
                ema_state[50],
                ema_state[100],
                ema_state[200],
                raw_volume_ratio,
                row.get("_raw_rsi_slope") or 0.0,
            )
            buy_conviction = calculate_buy_conviction(
                row.get("_raw_rsi") if row.get("_raw_rsi") is not None else 50.0,
                raw_volume_ratio,
                row.get("_raw_rsi_slope") or 0.0,
                market_semantics.get("momentum_confluence", ""),
            )

        if close_price is not None:
            last_close = close_price

        row["net_change"] = net_change
        row["percentage_change"] = percentage_change
        row["previous_close"] = previous_close
        row["prev_close_pct_diff"] = prev_close_pct_diff
        row["volume_avg_ratio"] = volume_avg_ratio
        for period in EMA_PERIODS:
            row[EMA_COLUMNS[period]] = ema_values[period]
        for key, column in BUY_CONVICTION_COLUMNS.items():
            row[column] = str(buy_conviction.get(key, ""))
        for key, column in MARKET_SEMANTICS_COLUMNS.items():
            row[column] = market_semantics.get(key, "")

    return valid_rows
