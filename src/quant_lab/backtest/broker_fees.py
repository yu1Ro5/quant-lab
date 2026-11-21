"""Webull日本の公表株式料金を固定したUSD試算（過去料金の再現ではない）。

確認日2026-11-21。FINRAの端数処理は公表資料で不明なためセント切上げを仮定。
CATは最小額・丸め規定が不明なため原額を保持する。整数株・単一即時約定のみ。
FINRAの2026年10〜12月免除とWebull公表株式料金の関係は未確認。
本モデルはWebull公表の通常株式料金を固定し、実請求額とは断定しない。
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

SCHEDULE = "2026-11-21"
SOURCES = {
    "pricing": "https://www.webull.co.jp/pricing",
    "service": "https://www.webull.co.jp/products/us-stocks/service",
    "commission": "https://www.webull.co.jp/blog/350",
    "cat": "https://www.webull.co.jp/blog/319",
    "sec": "https://www.webull.co.jp/blog/300",
    "finra": "https://www.webull.co.jp/blog/283",
}
EFFECTIVE_DATES = {
    "commission": "2026-07-27T17:30:00+09:00",
    "sec": "2026-04-02",
    "finra_rate": "2026-01-01",
    "cat": "2026-05-01",
}
D = Decimal


@dataclass(frozen=True)
class Fees:
    commission: Decimal = D(0)
    sec: Decimal = D(0)
    finra: Decimal = D(0)
    cat: Decimal = D(0)

    @property
    def total(self) -> Decimal:
        return self.commission + self.sec + self.finra + self.cat


def webull_fees(price: Decimal, shares: int, *, buy: bool, route: str) -> Fees:
    """実約定単価から公表料金を計算。売却SECのみ公表規則のセント切上げ。"""
    if route not in ("exchange", "otc"):
        raise ValueError("cat_route: exchange/otcを指定してください")
    if type(shares) is not int or shares < 0 or not price.is_finite() or price <= 0:
        raise ValueError("正の価格と非負の整数株数が必要です")
    if not shares:
        return Fees()
    cat = D(shares) * D("0.000003" if route == "exchange" else "0.00000003")
    if buy:
        return Fees(cat=cat)
    sec = max(D("0.00002060"), price * shares * D("0.00002060")).quantize(
        D("0.01"), rounding=ROUND_CEILING
    )
    finra = min(D("9.79"), max(D("0.01"), D(shares) * D("0.000195"))).quantize(
        D("0.01"), rounding=ROUND_CEILING
    )
    return Fees(sec=sec, finra=finra, cat=cat)


def fee_model_metadata() -> dict[str, object]:
    """確認済みの公表値と試算上の仮定・未解決事項を再現性成果物へ渡す。"""
    return {
        "model": "webull_jp_us_equity",
        "fee_schedule": SCHEDULE,
        "verified_on": SCHEDULE,
        "currency": "USD",
        "application": "現在の公表通常料金で過去の戦略を実行したと仮定する試算。過去の実料金の再現ではない。",
        "sources": SOURCES.copy(),
        "effective_dates": EFFECTIVE_DATES.copy(),
        "assumptions": [
            "米国株現物・買いポジション・整数株・単一即時約定・事前USD資金のみ。売買ごとの為替費用なし。",
            "CAT約定経路は設定cat_routeの仮定であり、OHLCVから観測した経路ではない。",
            "SECは売却実約定代金×0.00002060をセント切上げ。blog/300の最低0.01とサービス概要の最低0.00002060は、セント切上げ後に同じ0.01となると解釈。上限の公表なし。",
            "FINRAは売却株数×0.000195、最低0.01・最大9.79。端数規則は未確認のため約定ごとセント切上げを仮定。",
            "CATは両方向、exchange=0.000003/share、otc=0.00000003/share。最低額・上限・端数規則は未確認のため原額保持。",
            "FINRA公式の2026-10-01〜12-31 TAF免除とWebull日本の公表株式料金との適用関係は未確認。サービス概要の免除文言はオプションを指す。本試算はWebull公表通常株式料金を固定し実請求額と断定しない。",
        ],
        "regulatory_conflict_source": "https://www.sec.gov/files/rules/sro/finra/2026/34-106409-ex5.pdf",
    }
