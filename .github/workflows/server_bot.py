# -*- coding: utf-8 -*-
"""
서버용 24시간 자동매매 워커 (PC를 꺼도 돌아가는 버전의 심장)
=============================================================
- 웹앱(index.html)과 동일한 전략: 모멘텀+섹터 상위 4~6종목 총액 분할 보유,
  유연 회전(약세 보유 ↔ 강세 후보 교체), 목표 익절·트레일링·손절, 섹터당 최대 2종목
- 상태를 state.json에 저장 → 프로세스가 재시작돼도 계좌·포지션·기록을 이어받음
- 시세: Finnhub 무료 API 키 (분당 60회 한도 안에서 운영)
- 매매 기록은 trades.csv에 적립, 콘솔 로그 출력
- 미국 정규장(ET 기준) 중에만 매매하고, 장 밖에서는 대기

실행:
  export FINNHUB_KEY=xxxxxxxxxxxxxxxx        # (또는 --key 로 전달)
  python3 server_bot.py                      # 무한 루프 — 클라우드 VM·홈서버용
  python3 server_bot.py --once               # 1회만 실행 후 종료 — cron/GitHub Actions용
  python3 server_bot.py --sim --once         # 네트워크 없이 엔진 자가테스트

※ 학습/모의용입니다. 실주문 연동은 README 4장(증권사 API)을 참고하세요.
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

import urllib.parse
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
TRADES_CSV = os.path.join(HERE, "trades.csv")
UNIVERSE_FILE = os.path.join(HERE, "universe_us.json")

SECTOR_OF = {
    "AAPL": "빅테크", "MSFT": "빅테크", "AMZN": "빅테크", "GOOGL": "빅테크", "META": "빅테크",
    "NVDA": "반도체·AI", "AVGO": "반도체·AI", "AMD": "반도체·AI", "MU": "반도체·AI", "QCOM": "반도체·AI",
    "PLTR": "AI·소프트웨어", "COIN": "가상자산·핀테크", "HOOD": "가상자산·핀테크",
    "TSLA": "전기차·모빌리티", "UBER": "전기차·모빌리티", "XOM": "에너지", "JPM": "금융",
    "LLY": "의료·제약", "UNH": "의료·제약", "COST": "소비·유통", "MCD": "소비·유통",
    "NFLX": "엔터테인먼트", "BA": "산업·방산", "CAT": "산업·방산",
}
START_CASH, FEE = 10_000_000, 0.0005


# ───────────────────────── 상태 저장/복원 ─────────────────────────
def new_state(assets):
    return {
        "cash": START_CASH, "pos": {}, "realized": 0, "wins": 0, "closes": 0,
        "trades": 0, "rebal_count": 0, "last_rebal": 0, "flex_swaps": 0,
        "day_closes": {a["code"]: a["hist"][-200:] for a in assets},
        "peaks": {}, "created": datetime.now().isoformat(),
    }


def load_state(assets):
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        print(f"[상태] 이전 세션 복원 — 계좌 시작 {st['cash']:,.0f}$, 리밸런스 {st['rebal_count']}회 누적")
        return st
    st = new_state(assets)
    print("[상태] 신규 세션 시작")
    return st


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)          # ← 누락됐던 최종 교체 — 이제 계좌가 파일로 확정 저장됨


def log_trade(side, code, qty, px, reason, pnl=None):
    row = [datetime.now().strftime("%Y-%m-%d %H:%M"), side, code, f"{qty:.0f}",
           f"{px:.2f}", reason, "" if pnl is None else f"{pnl:.0f}"]
    new_file = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["시각", "구분", "종목", "수량", "가격", "사유", "손익$"])
        w.writerow(row)
    print(f"[{row[0]}] {side} {code} {qty:.0f}주 @ {px:,.2f}$ — {reason}"
          + (f" ({pnl:+,.0f}$)" if pnl is not None else ""))


# ───────────────────────── 시세 (Finnhub) ─────────────────────────
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def fetch_prices_alpaca(codes, key_id, secret):
    url = ("https://data.alpaca.markets/v2/stocks/snapshots?symbols="
           + urllib.parse.quote(",".join(codes)) + "&feed=iex")
    req = urllib.request.Request(url, headers={
        "APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret, "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read().decode())
    out = {}
    for sym, sn in j.items():
        if not isinstance(sn, dict):
            continue
        p = ((sn.get("latestTrade") or {}).get("p")
             or (sn.get("dailyBar") or {}).get("c")
             or (sn.get("prevDailyBar") or {}).get("c"))
        if p:
            out[sym.upper()] = float(p)
    return out


def fetch_prices_yahoo(codes):
    url = ("https://query1.finance.yahoo.com/v8/finance/spark?symbols="
           + urllib.parse.quote(",".join(codes)) + "&range=1d&interval=5m&_="
           + str(int(time.time() * 1000)))
    j = fetch_json(url)
    out = {}
    for r0 in (j.get("spark", {}).get("result") or []):
        resp = (r0.get("response") or [None])[0]
        meta = resp and resp.get("meta")
        if meta and isinstance(meta.get("regularMarketPrice"), (int, float)):
            out[meta["symbol"].upper()] = float(meta["regularMarketPrice"])
    return out


def get_prices(codes, a):
    """Alpaca(실시간, 기본) → 야후(대체) → Finnhub(키가 있으면 빈 종목 보강)"""
    out = {}
    if a.alpaca_id and a.alpaca_sec:
        try:
            out = fetch_prices_alpaca(codes, a.alpaca_id, a.alpaca_sec)
            print(f"[시세] Alpaca {len(out)}종목 수신")
        except Exception as e:
            print(f"[시세] Alpaca 실패 — 야후로 대체: {e}")
    if not out:
        try:
            out = fetch_prices_yahoo(codes)
            print(f"[시세] 야후 {len(out)}종목 수신")
        except Exception as e:
            print(f"[시세] 야후 실패: {e}")
    if a.key and len(out) < len(codes):
        fb = fetch_prices(codes, a.key)
        for c in fb:
            out.setdefault(c, fb[c])
    return out


def fetch_prices(codes, key):
    out = {}
    for i in range(0, len(codes), 8):                 # 분당 60회 한도 → 여유 있게
        chunk = codes[i:i+8]
        for c in chunk:
            try:
                j = fetch_json(f"https://finnhub.io/api/v1/quote?symbol={c}&token={key}")
                if isinstance(j, dict) and isinstance(j.get("c"), (int, float)) and j["c"] > 0:
                    out[c] = float(j["c"])
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError) as e:
                print(f"[시세] {c} 조회 실패: {e}")
        time.sleep(1)
    return out


# ───────────────────────── 전략 (웹앱과 동일 규칙) ─────────────────────────
def sma_last(arr, n):
    if len(arr) < n:
        return float("nan")
    return sum(arr[-n:]) / n


def mom(c):
    if len(c) < 60:
        return float("nan")
    return 0.5 * (c[-1] / c[-20] - 1) + 0.5 * (c[-1] / c[-60] - 1)


def run_cycle(st, prices, top_n=4, rebal_min=60, stop=-0.08, take=0.08, trail=0.05):
    now = time.time()
    # 날짜가 바뀌면 새 일봉을 이어붙이고(무기한 실행 대응), 같은 날엔 오늘 봉만 갱신
    today = ny_today()
    new_day = bool(today) and st.get("last_bar_date", "") != today
    for c, px in prices.items():
        arr = st["day_closes"].get(c)
        if not arr or px <= 0:
            continue
        if new_day:
            arr.append(px)
            if len(arr) > 200:
                arr.pop(0)
        else:
            arr[-1] = px
    if today:
        st["last_bar_date"] = today

    codes = list(st["day_closes"])
    closes = st["day_closes"]

    # 1) 손절·익절·트레일링
    for c in list(st["pos"]):
        px = prices.get(c)
        if not px or c not in st["pos"]:
            continue
        p = st["pos"][c]
        p["peak"] = max(p.get("peak", p["avg"]), px)
        ret = px / p["avg"] - 1
        r20 = mom(closes[c][:]) if len(closes[c]) >= 61 else float("nan")
        r60 = closes[c][-1] / closes[c][-60] - 1 if len(closes[c]) >= 60 else float("nan")
        if ret >= take:
            close_pos(st, c, px, f"목표 익절 +{ret*100:.1f}%")
        elif ret > 0 and px <= p["peak"] * (1 - trail):
            close_pos(st, c, px, f"트레일링 익절 — 고점 대비 {(px/p['peak']-1)*100:.1f}%")
        elif not math.isnan(r20) and r20 < 0 and r60 < 0:
            close_pos(st, c, px, f"모멘텀 소멸 이탈 ({r20*100:.1f}%)")
        elif ret <= stop:
            close_pos(st, c, px, f"손절 {ret*100:.1f}%")

    # 2) 유연 회전 + 정기 리밸런스
    if now - st["last_rebal"] >= rebal_min * 60:
        st["last_rebal"] = now
        st["flex_swaps"] = 0
        flex_rotate(st, prices, top_n)
        rebalance(st, prices, top_n)
    else:
        if st["flex_swaps"] < 2:
            flex_rotate(st, prices, top_n)

    save_state(st)


def equity(st, prices):
    inv = sum(p["qty"] * prices[c] for c, p in st["pos"].items() if c in prices)
    return st["cash"] + inv


def close_pos(st, code, px, reason):
    p = st["pos"].pop(code)
    gross = p["qty"] * px * (1 - FEE)
    pnl = gross - p["qty"] * p["avg"]
    st["cash"] += gross
    st["realized"] += pnl
    st["wins"] += pnl > 0
    st["closes"] += 1
    st["trades"] += 1
    log_trade("손절" if pnl <= 0 and "손절" in reason else "매도", code,
              p["qty"], px, reason, pnl)


def open_pos(st, code, px, qty, reason):
    cost = qty * px * (1 + FEE)
    if cost > st["cash"] or qty < 1:
        return False
    st["cash"] -= cost
    st["pos"][code] = {"qty": qty, "avg": cost / qty, "peak": px}
    st["trades"] += 1
    log_trade("매수", code, qty, px, reason)
    return True


def flex_rotate(st, prices, top_n):
    closes = st["day_closes"]
    # 약세 보유 먼저 정리
    for c in list(st["pos"]):
        m = mom(closes[c])
        if not math.isnan(m) and m < 0:
            close_pos(st, c, prices[c], f"회전 — 모멘텀 음전환({m*100:.1f}%)")
    # 최강 후보 편입
    if len(st["pos"]) < top_n:
        cand = ranked(st, prices, top_n)
        for x in cand:
            if x["code"] not in st["pos"] and len(st["pos"]) < top_n \
               and sum(1 for c in st["pos"] if SECTOR_OF[c] == SECTOR_OF[x["code"]]) < 2:
                if open_pos(st, x["code"], prices[x["code"]],
                            (equity(st, prices) / top_n) / prices[x["code"]], "유연 회전 편입"):
                    st["flex_swaps"] += 1


def rebalance(st, prices, top_n):
    st["rebal_count"] += 1
    picks = ranked(st, prices, top_n)[:top_n]
    picked = [x["code"] for x in picks]
    for c in list(st["pos"]):
        if c not in picked:
            close_pos(st, c, prices[c], f"리밸런스 #{st['rebal_count']} 랭킹 이탈")
    total = equity(st, prices)
    for x in picks:
        c = x["code"]
        open_pos(st, c, prices[c], (total / len(picks)) / prices[c], f"리밸런스 균등 배분 #{st['rebal_count']}")
    print(f"[리밸런스 #{st['rebal_count']}] 보유 {len(picked)}종목: {', '.join(picked)}")


def ranked(st, prices, top_n):
    closes = st["day_closes"]
    out = []
    for c in closes:
        if c not in prices:
            continue
        m = mom(closes[c])
        if math.isnan(m) or m <= 0:
            continue
        if closes[c][-1] <= sma_last(closes[c], 100):
            continue
        sec_members = [x for x in closes if SECTOR_OF[x] == SECTOR_OF[c]]
        heat = sum(mom(closes[x]) for x in sec_members
                   if not math.isnan(mom(closes[x]))) / max(1, len(sec_members))
        out.append({"code": c, "score": 0.6 * m + 0.4 * heat})
    out.sort(key=lambda x: -x["score"])
    res, cnt = [], {}
    for x in out:
        s = SECTOR_OF[x["code"]]
        if cnt.get(s, 0) >= 2:
            continue
        cnt[s] = cnt.get(s, 0) + 1
        res.append(x)
    return res


# ───────────────────────── 장중 확인 & 루프 ─────────────────────────
def ny_today():
    try:
        if ZoneInfo is None:
            return ""
        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return ""


def market_open_now():
    try:
        if ZoneInfo is None:
            return True
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return False
        return (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16
    except Exception:
        return True


def main():
    ap = argparse.ArgumentParser(description="24시간 서버 자동매매 워커")
    ap.add_argument("--alpaca_id", default=os.environ.get("ALPACA_ID", ""), help="Alpaca Key ID (기본 시세 소스)")
    ap.add_argument("--alpaca_sec", default=os.environ.get("ALPACA_SEC", ""), help="Alpaca Secret Key")
    ap.add_argument("--key", default=os.environ.get("FINNHUB_KEY", ""), help="Finnhub API 키 (선택)")
    ap.add_argument("--top", type=int, default=4, help="동시 보유 종목 수 (기본 4 — 최적화 1위값)")
    ap.add_argument("--rebal", type=int, default=20, help="리밸런스 주기(분, 기본 20 — 최적화 1위값)")
    ap.add_argument("--stop", type=float, default=-0.08, help="손절 (기본 -0.08)")
    ap.add_argument("--poll", type=int, default=60, help="시세 갱신 주기(초, 60 권장)")
    ap.add_argument("--once", action="store_true", help="1회 실행 후 종료 (cron/GitHub Actions용)")
    ap.add_argument("--sim", action="store_true", help="네트워크 없이 내장 가격으로 자가테스트")
    a = ap.parse_args()

    with open(UNIVERSE_FILE, encoding="utf-8") as f:
        assets = json.load(f)
    codes = [x["code"] for x in assets]
    names = {x["code"]: x["name"] for x in assets}
    st = load_state(assets)

    if a.sim:
        print("[자가테스트] 내장 가격으로 전 경로 1회 실행")
        prices = {x["code"]: x["hist"][-1] for x in assets}
        run_cycle(st, prices, a.top, 0, a.stop)     # rebal 0분 → 즉시 리밸런스
        prices2 = {c: p * 1.02 for c, p in prices.items()}
        run_cycle(st, prices2, a.top, 0, a.stop)    # +2% 가격으로 재실행 (익절 경로)
        print(f"\n[결과] 보유 {len(st['pos'])}종목 · 매매 {st['trades']}건 · "
              f"평가액 {equity(st, prices2):,.0f}$ · state.json/trades.csv 저장 완료")
        print("→ 다시 지우려면 state.json, trades.csv를 삭제하세요.")
        return



    print(f"[시작] 유니버스 {len(codes)}종목 · 보유 {a.top} · {a.rebal}분 리밸런스 · "
          f"{a.poll}초 갱신 — 장중에만 매매합니다")
    while True:
        if market_open_now():
            prices = get_prices(codes, a)
            if prices:
                run_cycle(st, prices, a.top, a.rebal, a.stop)
        else:
            print(f"[{datetime.now():%H:%M}] 장 마감 — 대기 중")
            save_state(st)
        if a.once:
            return
        time.sleep(a.poll if market_open_now() else 300)


if __name__ == "__main__":
    main()
