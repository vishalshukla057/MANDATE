from typing import Any, Dict, List


class FXService:
    """Multi-currency exchange rate risk manager and dynamic discounting engine."""

    # Reference exchange rates against USD
    RATES_TO_USD = {
        "USD": 1.0,
        "EUR": 1.08,
        "GBP": 1.28,
        "CAD": 0.74,
        "AUD": 0.66,
        "JPY": 0.0067,
    }

    # 30-day projected volatility factors
    VOLATILITY_FACTOR = {
        "USD": 0.0,
        "EUR": 0.024,
        "GBP": 0.031,
        "CAD": 0.018,
        "AUD": 0.028,
        "JPY": 0.045,
    }

    @classmethod
    def convert_to_usd(cls, amount: float, currency: str) -> float:
        rate = cls.RATES_TO_USD.get(currency.upper(), 1.0)
        return round(amount * rate, 2)

    @classmethod
    def analyze_fx_risk(cls, amount: float, currency: str, days_overdue: int) -> Dict[str, Any]:
        curr = currency.upper()
        rate = cls.RATES_TO_USD.get(curr, 1.0)
        vol = cls.VOLATILITY_FACTOR.get(curr, 0.015)
        usd_value = round(amount * rate, 2)

        # Projected exposure increases with days overdue
        projected_downside_pct = min(0.08, round(vol * (1.0 + (days_overdue / 30.0)), 3))
        projected_loss_usd = round(usd_value * projected_downside_pct, 2)

        # Dynamic early settlement incentive
        recommended_discount_pct = min(10.0, round(projected_downside_pct * 100 * 1.5, 1))

        return {
            "invoice_currency": curr,
            "invoice_amount": amount,
            "current_usd_value": usd_value,
            "exchange_rate": rate,
            "projected_volatility_pct": round(vol * 100, 2),
            "projected_currency_loss_usd": projected_loss_usd,
            "recommended_fx_lock_discount_pct": recommended_discount_pct,
            "incentive_recovery_usd": round(usd_value * (1.0 - (recommended_discount_pct / 100.0)), 2),
            "recommendation": (
                f"Propose a {recommended_discount_pct}% dynamic lock-in discount if paid within 48h to avoid "
                f"projected ${projected_loss_usd} USD currency depreciation."
            ) if curr != "USD" else "Zero FX volatility: standard USD account.",
        }

