def generate_market_semantics(
    close_price,
    ema_9,
    ema_21,
    ema_50,
    ema_100,
    ema_200,
    volume_ratio,
    rsi_slope=0.0,
):
    # Round numeric inputs to 2 decimal places for consistency
    rsi_slope = round(rsi_slope, 2)
    volume_ratio = round(volume_ratio, 2)

    # ==========================================
    # 1. VOLUME DESCRIPTION
    # ==========================================
    if volume_ratio >= 3.5:
        pct = int((volume_ratio - 1) * 100)
        vol_desc = f'Climactic (+{pct}% vs avg) — exhaustion or massive breakout confirmation.'
    elif volume_ratio >= 2.0:
        pct = int((volume_ratio - 1) * 100)
        vol_desc = f'Exceptional (+{pct}% vs avg) — strong institutional conviction.'
    elif volume_ratio >= 1.2:
        pct = int((volume_ratio - 1) * 100)
        vol_desc = f'Elevated (+{pct}% vs avg) — active participation.'
    elif volume_ratio >= 0.8:
        vol_desc = 'Normal — hovering near historical average.'
    else:
        pct = int((1 - volume_ratio) * 100)
        vol_desc = f'Dried up (-{pct}% vs avg) — low liquidity / quiet pullback.'

    # ==========================================
    # 2. RSI SLOPE VELOCITY
    # ==========================================
    if rsi_slope >= 2.0:
        slope_desc = f'Hot (+{rsi_slope:.2f}/bar) — aggressive momentum surge.'
    elif rsi_slope >= 0.5:
        slope_desc = f'Positive (+{rsi_slope:.2f}/bar) — steady momentum expansion.'
    elif rsi_slope >= -0.5:
        slope_desc = f'Neutral ({rsi_slope:+.2f}/bar) — sideways/stalled momentum.'
    elif rsi_slope >= -2.0:
        slope_desc = f'Cooling ({rsi_slope:+.2f}/bar) — momentum fading.'
    else:
        slope_desc = f'Crashing ({rsi_slope:+.2f}/bar) — sharp momentum washout.'

    # ==========================================
    # 3. MACRO & TACTICAL STATE EVALUATION
    # ==========================================
    macro_bull = close_price > ema_200
    macro_str = 'Bullish (above 200 EMA)' if macro_bull else 'Bearish (below 200 EMA)'

    full_bull_stack = close_price > ema_9 > ema_21 > ema_50 > ema_100 > ema_200
    full_bear_stack = close_price < ema_9 < ema_21 < ema_50 < ema_100 < ema_200

    if close_price > (ema_9 * 1.15) and ema_9 > ema_21:
        tactical_desc = 'Severely overextended upside; high mean-reversion pull risk.'
    elif full_bull_stack:
        tactical_desc = (
            'Perfect institutional bull stack across all tracked timeframes.'
        )
    elif full_bear_stack:
        tactical_desc = 'Perfect institutional bear stack; heavy overhead supply.'
    elif ema_50 > ema_100 > ema_200 and close_price <= ema_50:
        tactical_desc = (
            'Pullback into high-confluence 100/200 EMA support zone.'
            if close_price >= ema_200
            else 'WARNING: Aggressive violation of 200 EMA despite lagging structure.'
        )
    elif ema_50 < ema_100 < ema_200 and close_price >= ema_50:
        tactical_desc = (
            'Counter-trend relief rally pressing major 100/200 EMA resistance.'
            if close_price <= ema_200
            else 'MAJOR SHIFT: Reclaimed 200 EMA, challenging macro bear trend.'
        )
    elif ema_50 > ema_200 and (ema_9 < ema_50 or close_price < ema_50):
        tactical_desc = 'Post-Golden Cross consolidation / shakeout phase.'
    elif close_price > ema_9 > ema_21 > ema_50:
        tactical_desc = 'Mid-tier bullish stack; steady short-to-mid acceleration.'
    elif close_price < ema_9 < ema_21 < ema_50:
        tactical_desc = 'Mid-tier bearish stack; short-to-mid downward momentum.'
    elif ema_9 > ema_21 and close_price > ema_9 and ema_21 <= ema_50:
        tactical_desc = 'Early-stage bullish breakout attempt.'
    elif ema_9 < ema_21 and close_price < ema_9 and ema_21 >= ema_50:
        tactical_desc = 'Early-stage bearish breakdown / structural rollover warning.'
    else:
        tactical_desc = 'Compressed/flattening MAs; accumulation-distribution range.'

    # ==========================================
    # 4. CROSS-CONFLUENCE INSIGHTS (The Edge)
    # ==========================================
    is_bullish_context = macro_bull or full_bull_stack
    if is_bullish_context and rsi_slope < -1.0:
        confluence = 'DIVERGENCE WARNING: Bullish structural context, but momentum is aggressively flushing down.'
    elif not is_bullish_context and rsi_slope > 1.0:
        confluence = 'INFLECTION ALERT: Bearish/choppy context, but aggressive dip-buying momentum is igniting.'
    elif (full_bull_stack and rsi_slope > 0.5) or (
        full_bear_stack and rsi_slope < -0.5
    ):
        confluence = 'ALIGNED: Momentum velocity confirms structural stack.'
    else:
        confluence = 'NEUTRAL / MIXED: Momentum velocity lacks structural override.'

    return {
        'macro_context': macro_str,
        'tactical_state': tactical_desc,
        'volume_description': vol_desc,
        'rsi_slope_description': slope_desc,
        'momentum_confluence': confluence,
        'human_readable': f'[Macro: {macro_str}] | {tactical_desc} | {confluence}',
    }
