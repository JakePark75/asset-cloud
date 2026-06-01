def fmt_krw(val):
    v = int(val)
    if abs(v) >= 100_000_000:
        return f"{v / 100_000_000:,.1f}억"
    elif abs(v) >= 10_000:
        return f"{v / 10_000:,.0f}만"
    return f"{v:,}"

def fmt_10m(val):
    return f"{float(val) / 100_000_000:.2f}억"