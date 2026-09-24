def calculate_buy_conviction(rsi, vol, slope, conf):
    """Calculates the short-term buy-reversion conviction probability from raw indicator values.

    Args:
        rsi (float): RSI(9) value.
        vol (float): volume / 21-day-average-volume ratio.
        slope (float): RSI(9) OLS slope.
        conf (str): market_momentum_confluence description text.

    Returns:
        dict: Contains probability_percent and reason breakdown.
    """
    # Round numeric inputs to 2 decimal places for consistency
    rsi = round(rsi, 2)
    vol = round(vol, 2)
    slope = round(slope, 2)

    conf = conf or ""
    score = 0
    reason_parts = []

    # 1. Momentum Inflection (confluence-flagged only; positive slope alone is
    #    scored separately in section 3, so it isn't double-counted here)
    if "INFLECTION ALERT" in conf:
        score += 45
        reason_parts.append("Positive RSI slope/inflection confirmation")
    elif rsi < 20:
        score += 35
        reason_parts.append(f"Extreme oversold washout (RSI {rsi})")
    elif rsi < 30:
        score += 25
        reason_parts.append(f"Oversold zone (RSI {rsi})")
    else:
        score += 10
        reason_parts.append(f"Moderate RSI ({rsi})")

    # 2. Volume Participation Surge
    if vol > 2.5:
        score += 40
        reason_parts.append(f"Exceptional volume surge ({vol}x avg)")
    elif vol > 1.8:
        score += 30
        reason_parts.append(f"High volume participation ({vol}x avg)")
    elif vol > 1.4:
        score += 20
        reason_parts.append(f"Elevated volume ({vol}x avg)")
    else:
        score += 10
        reason_parts.append(f"Normal volume ({vol}x avg)")

    # 3. Slope Trajectory
    if slope > 0:
        score += 15
        reason_parts.append(f"Expanding RSI slope ({slope:+.2f})")
    else:
        reason_parts.append(f"Contracting/flat slope ({slope:+.2f})")

    probability = min(100, score)
    tag = conf.split(":")[0] if ":" in conf else conf or "GENERAL"

    return {
        "probability_percent": probability,
        "reason": f"{'; '.join(reason_parts)} | [{tag}]",
    }
