def calculate_rsi(closes, period):
    n = len(closes)
    rsi_values = [None] * n
    if n < period + 1:
        return rsi_values

    gains = []
    losses = []
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_values[period] = round(_rsi_from_averages(avg_gain, avg_loss), 2)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_values[i + 1] = round(_rsi_from_averages(avg_gain, avg_loss), 2)

    return rsi_values


def _rsi_from_averages(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(highs, lows, closes, period):
    n = len(highs)
    atr_values = [None] * n
    if n < period:
        return atr_values

    true_ranges = []
    for i in range(n):
        if i == 0:
            true_range = highs[i] - lows[i]
        else:
            true_range = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        true_ranges.append(true_range)

    atr = sum(true_ranges[:period]) / period
    atr_values[period - 1] = round(atr, 2)

    for i in range(period, n):
        atr = (atr * (period - 1) + true_ranges[i]) / period
        atr_values[i] = round(atr, 2)

    return atr_values


def calculate_rsi_slope(rsi_values, period):
    n = len(rsi_values)
    slopes = [None] * n
    if n < period:
        return slopes

    x_mean = (period - 1) / 2
    x_deviations = [i - x_mean for i in range(period)]
    denominator = sum(dx * dx for dx in x_deviations)
    if not denominator:
        return slopes

    for i in range(period - 1, n):
        window = rsi_values[i - period + 1: i + 1]
        if any(value is None for value in window):
            continue
        y_mean = sum(window) / period
        numerator = sum(dx * (y - y_mean) for dx, y in zip(x_deviations, window))
        slopes[i] = round(numerator / denominator, 2)

    return slopes


def calculate_donchian_channels(highs, lows, period=20):
    """Calculate Donchian Channels (rolling support/resistance).

    Returns upper and lower bands representing the highest high and lowest low
    over the rolling period.

    Args:
        highs: List of high prices
        lows: List of low prices
        period: Rolling window period (default 20)

    Returns:
        tuple: (upper_band, lower_band) - lists of resistance and support values
    """
    n = len(highs)
    upper_band = [None] * n
    lower_band = [None] * n

    if n < period:
        return upper_band, lower_band

    for i in range(period - 1, n):
        window_highs = highs[i - period + 1: i + 1]
        window_lows = lows[i - period + 1: i + 1]
        upper_band[i] = round(max(window_highs), 2)
        lower_band[i] = round(min(window_lows), 2)

    return upper_band, lower_band
