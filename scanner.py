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
    latest_bar = prices[-1]
    stock["price"] = close
    stock["change"] = round((close / closes[-2] - 1) * 100, 2) if len(closes) > 1 else 0
    stock["volume"] = volumes[-1]
    stock["value"] = close * volumes[-1] / 1_000_000
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / min(60, len(closes))
    high20 = max(closes[-20:])
    low20 = min(closes[-20:])
    vol5 = sum(volumes[-5:]) / max(1, len(volumes[-5:]))
    vol20 = sum(volumes[-20:]) / max(1, len(volumes[-20:]))
    trend = 25 if close > ma20 else 5
    momentum = min(25, max(0, 12.5 + stock["change"] * 2.5))
    liquidity = min(20, max(0, stock["value"] / 5000))
    volume = min(15, 7.5 * vol5 / max(vol20, 1))
    structure = 15 if close >= high20 * 0.97 else (8 if close >= ma5 else 2)
    score = round(min(100, trend + momentum + liquidity + volume + structure))
    vol_ratio = vol5 / max(vol20, 1)
    high_gap = (close / high20 - 1) * 100
    ma20_gap = (close / ma20 - 1) * 100
    range_position = (close - low20) / max(high20 - low20, 1) * 100

    if close > ma20 and ma5 > ma20 and score >= 70:
        strength = "강함"
    elif close > ma20 or score >= 55:
        strength = "보통"
    else:
        strength = "약함"

    if ma20_gap >= 12 or (range_position >= 97 and stock["change"] >= 5):
        position = "과열권"
    elif close >= high20 * 0.97 and ma5 >= ma20:
        position = "상승 진행"
    elif close >= ma20 * 0.98 and ma5 >= ma20:
        position = "눌림 구간"
    elif close > ma60 and ma5 > ma20:
        position = "상승 초입"
    elif close <= ma20:
        position = "약세 구간"
    else:
        position = "바닥 준비"

    if strength == "강함" and position in ("눌림 구간", "상승 초입") and vol_ratio >= 0.8:
        action = "소액 검토"
        action_reason = "힘이 강하고 추격 부담이 비교적 작아요"
    elif strength == "강함" and position in ("상승 진행", "과열권"):
        action = "눌림 대기"
        action_reason = "종목은 강하지만 지금은 추격보다 눌림을 기다려요"
    elif strength == "약함" and position == "약세 구간":
        action = "제외"
        action_reason = "아직 가격 구조가 약해요"
    else:
        action = "관찰"
        action_reason = "방향이 더 분명해질 때까지 지켜봐요"

    return {
        **stock,
        "score": score,
        "day_high": latest_bar["high"],
        "day_low": latest_bar["low"],
        "strength": strength,
        "position": position,
        "action": action,
        "action_reason": action_reason,
        "reason": f"20일선 대비 {ma20_gap:+.1f}% · 거래량 {vol_ratio:.1f}배 · 20일 고점 대비 {high_gap:.1f}%",
    }


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
        shown = ranked[:10]
        action_counts = {name: sum(1 for item in shown if item["action"] == name) for name in ("소액 검토", "눌림 대기", "관찰", "제외")}
        data = {"ok": True, "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "market_state": "관찰", "count": len(ranked), "action_counts": action_counts, "stocks": shown}
    except Exception as exc:
        data = {"ok": False, "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "market_state": "연결 점검", "count": 0, "stocks": [], "message": str(exc)}
    CACHE.update({"at": time.time(), "data": data})
    return data


if __name__ == "__main__":
    result = scan_market()
    now = datetime.now(KST)
    base = Path(__file__).parent / "data"
    output = base / "latest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    session = "open" if now.hour < 12 else "close"
    history_dir = base / "history" / now.strftime("%Y-%m-%d")
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{session}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if session == "close":
        open_file = history_dir / "open.json"
        if open_file.exists():
            morning = json.loads(open_file.read_text(encoding="utf-8"))
            closing = {item["code"]: item for item in result.get("stocks", [])}
            rows = []
            for first in morning.get("stocks", []):
                last = closing.get(first["code"])
                if not last or not first.get("price"):
                    continue
                price = first["price"]
                rows.append({
                    "code": first["code"], "name": first["name"],
                    "morning_action": first["action"], "first_price": price,
                    "close_price": last["price"],
                    "close_return": round((last["price"] / price - 1) * 100, 2),
                    "day_peak_return": round((last["day_high"] / price - 1) * 100, 2),
                    "mae": round((last["day_low"] / price - 1) * 100, 2),
                })
            report = {"date": now.strftime("%Y-%m-%d"), "open_updated_at": morning.get("updated_at"), "close_updated_at": result.get("updated_at"), "stocks": rows}
            report_dir = base / "daily-reports"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / f"{now:%Y-%m-%d}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output}")
