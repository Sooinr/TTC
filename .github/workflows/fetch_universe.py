# -*- coding: utf-8 -*-
"""실제 국내 대표 종목 12개의 최근 시세(약 15개월)를 조회해 universe.json 생성.
웹앱(index.html)에 내장할 실제 시세 히스토리 + 변동성/시총 데이터를 만든다."""
import json, math

import pandas as pd

UNIVERSE = {
    "005930.KS": ("삼성전자", "반도체"),
    "000660.KS": ("SK하이닉스", "반도체"),
    "373220.KS": ("LG에너지솔루션", "2차전지"),
    "006400.KS": ("삼성SDI", "2차전지"),
    "247540.KS": ("에코프로비엠", "2차전지소재"),
    "035420.KS": ("NAVER", "인터넷"),
    "035760.KS": ("카카오", "인터넷"),
    "005380.KS": ("현대차", "자동차"),
    "329180.KS": ("HD현대중공업", "조선"),
    "051905.KS": ("LG화학", "화학"),
    "207940.KS": ("삼성바이오로직스", "바이오"),
    "012330.KS": ("삼성물산", "건설·유통"),
}

import yfinance as yf

rows = []
for code, (name, sector) in UNIVERSE.items():
    df = yf.download(code, period="15mo", interval="1d",
                     progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    closes = df["Close"].dropna()
    if len(closes) < 180:
        print(f"!! {code} {name}: 데이터 부족({len(closes)}일) — 스킵")
        continue
    ret = closes.pct_change().dropna()
    vol = float(ret.std())
    cap = None
    try:                                  # 시총(조원) — 실패해도 계속
        fi = yf.Ticker(code).fast_info
        cap = round(float(fi["market_cap"]) / 1e12, 1)
    except Exception:
        pass
    rows.append({
        "code": code.replace(".KS", ""), "name": name, "sector": sector,
        "cap": cap, "vol": round(vol, 5),
        "p0": float(closes.iloc[-1]),
        "hist": [round(float(x)) for x in closes.iloc[-200:]],
    })
    print(f"ok {code} {name}: {len(closes)}일, 마지막 {float(closes.iloc[-1]):,.0f}원, "
          f"일변동성 {vol:.2%}, 시총 {cap}조")

with open("universe.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, ensure_ascii=False, separators=(",", ":"))
print(f"\n→ universe.json 저장: {len(rows)}종목, 크기 {math.ceil(len(json.dumps(rows))/1024)}KB")
