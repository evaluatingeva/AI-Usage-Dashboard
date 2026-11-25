"""Cost computation for events that arrive without a provider-reported cost."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PriceCatalog


class PriceBook:
    def __init__(self, db: Session):
        self._rows = list(db.scalars(select(PriceCatalog).order_by(PriceCatalog.effective_from.desc())))

    def price(self, provider: str, model: str | None, unit_type: str, on: date) -> float | None:
        model = (model or "").lower()
        best, best_len = None, -1
        for r in self._rows:
            if r.provider != provider or r.unit_type != unit_type or r.effective_from > on:
                continue
            pat = r.model.lower()
            if pat == model:
                return float(r.price_per_unit_usd)
            if pat.endswith("*") and model.startswith(pat[:-1]) and len(pat) > best_len:
                best, best_len = float(r.price_per_unit_usd), len(pat)
        return best

    def cost(self, ev) -> float | None:
        """Return cost in USD, or None if no price is known."""
        day = ev.ts.date()
        total, known = 0.0, False
        parts = [
            ("input_tokens", ev.input_tokens),
            ("output_tokens", ev.output_tokens),
            ("cached_input_tokens", ev.cached_input_tokens),
        ]
        if ev.unit_type and ev.quantity:
            parts.append((ev.unit_type, ev.quantity))
        for unit, qty in parts:
            if not qty:
                continue
            p = self.price(ev.provider, ev.model, unit, day)
            if p is None and unit == "cached_input_tokens":
                p = self.price(ev.provider, ev.model, "input_tokens", day)
            if p is not None:
                total += p * qty
                known = True
        return total if known else None
