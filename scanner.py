from __future__ import annotations

from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE = {"at": 0.0, "data": None}
KST = timezone(timedelta(hours=9))

# 첫 검증용 고유동성 대표 종목. 전 종목 확대는 점수 검증 뒤 진행한다.
UNIVERSE = [
    ("005930", "삼성전자", "KOSPI"), ("000660", "SK하이닉스", "KOSPI"),
    ("373220", "LG에너지솔루션", "KOSPI"), ("207940", "삼성바이오로직스", "KOSPI"),
    ("005380", "현대차", "KOSPI"), ("000270", "기아", "KOSPI"),
    ("068270", "셀트리온", "KOSPI"), ("105560", "KB금융", "KOSPI"),
    ("055550", "신한지주", "KOSPI"), ("035420", "NAVER", "KOSPI"),
    ("035720", "카카오", "KOSPI"), ("012330", "현대모비스", "KOSPI"),
    ("005490", "POSCO홀딩스", "KOSPI"), ("028260", "삼성물산", "KOSPI"),
    ("006400", "삼성SDI", "KOSPI"), ("051910", "LG화학", "KOSPI"),
    ("096770", "SK이노베이션", "KOSPI"), ("034020", "두산에너빌리티", "KOSPI"),
    ("009540", "HD한국조선해양", "KOSPI"), ("012450", "한화에어로스페이스", "KOSPI"),
    ("042660", "한화오션", "KOSPI"), ("086790", "하나금융지주", "KOSPI"),
    ("032830", "삼성생명", "KOSPI"), ("015760", "한국전력", "KOSPI"),
    ("247540", "에코프로비엠", "KOSDAQ"), ("086520", "에코프로", "KOSDAQ"),
    ("196170", "알테오젠", "KOSDAQ"), ("028300", "HLB", "KOSDAQ"),
    ("263750", "펄어비스", "KOSDAQ"), ("293490", "카카오게임즈", "KOSDAQ"),
]


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
    response = requests.get(url, params={"symbol": code, "timeframe": "day", "count": count, "requestType": 0}, headers=HEADERS, timeout=8)
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
    stock["price"] = close
    stock["change"] = round((close / closes[-2] - 1) * 100, 2) if len(closes) > 1 else 0
    stock["volume"] = volumes[-1]
    stock["value"] = close * volumes[-1] / 1_000_000
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
        universe = [{"code": code, "name": name, "market": market, "price": 0, "change": 0, "volume": 0, "value": 0} for code, name, market in UNIVERSE]
        ranked = []
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(_score, stock) for stock in universe]
            for future in as_completed(futures):
                try:
                    item = future.result()
                    if item:
                        ranked.append(item)
                except Exception:
                    continue
        ranked.sort(key=lambda x: (x["score"], x["value"]), reverse=True)
        data = {"ok": True, "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "market_state": "관찰", "count": len(ranked), "stocks": ranked[:10]}
    except Exception as exc:
        data = {"ok": False, "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "market_state": "연결 점검", "count": 0, "stocks": [], "message": str(exc)}
    CACHE.update({"at": time.time(), "data": data})
    return data


if __name__ == "__main__":
    result = scan_market()
    output = Path(__file__).parent / "data" / "latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output}")
