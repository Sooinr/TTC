# -*- coding: utf-8 -*-
"""미국 대형주 12개의 최근 시세(약 15개월)를 조회해 universe_us.json 생성.
웹앱(index.html)에 내장할 실제 시세 히스토리 + 변동성/시총 데이터를 만든다."""
import json
import math

import pandas as pd
import yfinance as yf

UNIVERSE = {
    "AAPL": ("애플", "빅테크"),
    "MSFT": ("마이크로소프트", "빅테크"),
    "NVDA": ("엔비디아", "반도체·AI"),
    "AMZN": ("아마존", "빅테크"),
    "GOOGL": ("알파벳", "빅테크"),
    "META": ("메타", "빅테크"),
    "TSLA": ("테슬라", "전기차·모빌리티"),
    "AVGO": ("브로드컴", "반도체·AI"),
    "AMD": ("AMD", "반도체·AI"),
    "JPM": ("JP모건", "금융"),
    "COST": ("코스트코", "소비·유통"),
    "NFLX": ("넷플릭스", "엔터테인먼트"),
    "PLTR": ("팔런티어", "AI·소프트웨어"),
    "MU": ("마이크론", "반도체·AI"),
    "QCOM": ("퀄컴", "반도체·AI"),
    "COIN": ("코인베이스", "가상자산·핀테크"),
    "HOOD": ("로빈후드", "가상자산·핀테크"),
    "UBER": ("우버", "전기차·모빌리티"),
    "XOM": ("엑슨모빌", "에너지"),
    "LLY": ("일라이릴리", "의료·제약"),
    "UNH": ("유나이티드헬스", "의료·제약"),
    "MCD": ("맥도날드", "소비·유통"),
    "BA": ("보잉", "산업·방산"),
    "CAT": ("캐터필러", "산업·방산"),
}

rows = []
for code, (name, sector) in UNIVERSE.items():
    try:
        df = yf.download(code, period="15mo", interval="1d",
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        closes = df["Close"].dropna()
        if len(closes) < 180:
            print(f"!! {code} {name}: 데이터 부족({len(closes)}일) — 스킵")
            continue
        vol = float(closes.pct_change().dropna().std())
        cap = None
        try:
            cap = round(float(yf.Ticker(code).fast_info["market_cap"]) / 1e9, 0)  # $B
        except Exception:
            pass
        rows.append({
            "code": code, "name": name, "sector": sector,
            "cap": cap, "vol": round(vol, 5),
            "p0": float(closes.iloc[-1]),
            "hist": [round(float(x), 2) for x in closes.iloc[-200:]],
        })
        print(f"ok {code} {name}: {len(closes)}일, 마지막 ${float(closes.iloc[-1]):,.2f}, "
              f"일변동성 {vol:.2%}, 시총 ${cap:,.0f}B")
    except Exception as e:
        print(f"!! {code} 조회 실패: {e}")

with open("universe_us.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
print(f"\n→ universe_us.json 저장: {len(rows)}종목, "
      f"크기 {math.ceil(len(json.dumps(rows)) / 1024)}KB")
