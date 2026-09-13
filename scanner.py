from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE = {"at": 0.0, "data": None}


def _number(text: str) -> float:
    try:
        return float(text.replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _market_page(market: int, page: int):
    url = "https://finance.naver.com/sise/sise_market_sum.naver"
    html = requests.get(url, params={"sosok": market, "page": page}, headers=HEADERS, timeout=15)
    html.raise_for_status()
    soup = BeautifulSoup(html.text, "html.parser")
    rows = []
    for tr in soup.select("table.type_2 tr"):
        link = tr.select_one("a.tltle")
        cells = tr.select("td.number")
        if not link or len(cells) < 9:
            continue
        code = link.get("href", "").split("code=")[-1]
        rows.append({
            "code": code,
            "name": link.get_text(strip=True),
            "price": _number(cells[0].get_text()),
            "change": _number(cells[2].get_text()),
            "volume": _number(cells[6].get_text()),
            "value": _number(cells[7].get_text()),
            "market": "KOSPI" if market == 0 else "KOSDAQ",
        })
    return rows


def _daily_prices(code: str, count: int = 70):
    url = "https://fchart.stock.naver.com/sise.nhn"
    response = requests.get(url, params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0}, headers=HEADERS, timeout=15)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    prices = []
    for item in root.findall(".//item"):
        parts = item.attrib.get("data", "").split("|")
        if len(parts) >= 6:
            prices.append({"date": parts[0], "open": _number(parts[1]), "high": _number(parts[2]), "low": _number(parts[3]), "close": _number(parts[4]), "volume": _number(parts[5])})
    return prices


def _score(stock):
    prices = _daily_prices(stock["code"])
    closes = [p["close"] for p in prices if p["close"]]
    volumes = [p["volume"] for p in prices if p["volume"]]
    if len(closes) < 25:
        return None
    close = closes[-1]
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    high20 = max(closes[-20:])
    vol5 = sum(volumes[-5:]) / max(1, len(volumes[-5:]))
    vol20 = sum(volumes[-20:]) / max(1, len(volumes[-20:]))
    trend = 25 if close > ma20 else 5
    momentum = min(25, max(0, 12.5 + stock["change"] * 2.5))
    liquidity = min(20, max(0, stock["value"] / 5000))
    volume = min(15, 7.5 * vol5 / max(vol20, 1))
    structure = 15 if close >= high20 * 0.97 else (8 if close >= ma5 else 2)
    score = round(min(100, trend + momentum + liquidity + volume + structure))
    action = "진입 후보" if score >= 85 and stock["change"] < 12 else "눌림 대기" if score >= 75 else "관찰"
    return {**stock, "score": score, "action": action, "reason": f"20일선 {'위' if close > ma20 else '아래'} · 거래량 {vol5 / max(vol20, 1):.1f}배 · 20일 고점 대비 {((close / high20)-1)*100:.1f}%"}


def scan_market():
    if CACHE["data"] and time.time() - CACHE["at"] < 600:
        return CACHE["data"]
    try:
        universe = []
        for market in (0, 1):
            universe.extend(_market_page(market, 1))
            universe.extend(_market_page(market, 2))
        universe = sorted(universe, key=lambda x: x["value"], reverse=True)[:80]
        ranked = []
        for stock in universe:
            try:
                item = _score(stock)
                if item:
                    ranked.append(item)
            except Exception:
                continue
        ranked.sort(key=lambda x: (x["score"], x["value"]), reverse=True)
        data = {"ok": True, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "market_state": "관찰", "count": len(ranked), "stocks": ranked[:10]}
    except Exception as exc:
        data = {"ok": False, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "market_state": "연결 점검", "count": 0, "stocks": [], "message": str(exc)}
    CACHE.update({"at": time.time(), "data": data})
    return data


if __name__ == "__main__":
    result = scan_market()
    output = Path(__file__).parent / "data" / "latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output}")
