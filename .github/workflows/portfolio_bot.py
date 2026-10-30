# -*- coding: utf-8 -*-
"""
포트폴리오 모멘텀 로테이션 백테스트 (실제 시세)
================================================
전략:
  - 유니버스: 국내 대표 종목 12개 (아래 UNIVERSE)
  - 매 리밸런스 주기(기본 20거래일)마다 모멘텀 스코어(20일·60일 수익률 혼합) 상위
    N개(기본 5, 4~6 가능) 종목 선정 → 총자산을 종목당 1/N씩 동일 분할 투자 (총액 기반)
  - 추세 필터: 100일 이평 아래 or 스코어 0 이하면 제외 → 통과 종목이 적으면 현금 보유
  - 방어 모드: 코스피지수(^KS11)가 200일 이평 아래면 보유 종목 수 절반으로 축소
  - 개별 손절: 매입가 대비 -10% 도달 시 즉시 매도
  - 비용: 수수료 0.015%, 매도 거래세 0.15%

실행:
  python3 portfolio_bot.py                       # 기본: 보유 5종목, 20거래일 리밸런스
  python3 portfolio_bot.py --top 8 --rebal 10    # 8종목 / 2주 리밸런스
  python3 portfolio_bot.py --top 4 --weight momentum
"""
import argparse
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
except ImportError:
    plt = None

UNIVERSE = {
    "005930.KS": "삼성전자",   "000660.KS": "SK하이닉스",
    "373220.KS": "LG에너지솔루션", "006400.KS": "삼성SDI",
    "247540.KS": "에코프로비엠", "035420.KS": "NAVER",
    "005380.KS": "현대차",     "329180.KS": "HD현대중공업",
    "051905.KS": "LG화학",     "207940.KS": "삼성바이오로직스",
    "012330.KS": "삼성물산",
}
US_UNIVERSE = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "NVDA": "엔비디아",
    "AMZN": "아마존", "GOOGL": "알파벳", "META": "메타",
    "TSLA": "테슬라", "AVGO": "브로드컴", "AMD": "AMD",
    "JPM": "JP모건", "COST": "코스트코", "NFLX": "넷플릭스",
    "PLTR": "팔런티어", "MU": "마이크론", "QCOM": "퀄컴",
    "COIN": "코인베이스", "HOOD": "로빈후드", "UBER": "우버",
    "XOM": "엑슨모빌", "LLY": "일라이릴리", "UNH": "유나이티드헬스",
    "MCD": "맥도날드", "BA": "보잉", "CAT": "캐터필러",
}
US_SECTOR_OF = {
    "AAPL": "빅테크", "MSFT": "빅테크", "AMZN": "빅테크",
    "GOOGL": "빅테크", "META": "빅테크",
    "NVDA": "반도체·AI", "AVGO": "반도체·AI", "AMD": "반도체·AI",
    "MU": "반도체·AI", "QCOM": "반도체·AI",
    "PLTR": "AI·소프트웨어", "COIN": "가상자산·핀테크", "HOOD": "가상자산·핀테크",
    "TSLA": "전기차·모빌리티", "UBER": "전기차·모빌리티",
    "XOM": "에너지", "JPM": "금융", "LLY": "의료·제약", "UNH": "의료·제약",
    "COST": "소비·유통", "MCD": "소비·유통", "NFLX": "엔터테인먼트",
    "BA": "산업·방산", "CAT": "산업·방산",
}
US_SECTORS = {}
for _c, _s in US_SECTOR_OF.items():
    US_SECTORS.setdefault(_s, []).append(_c)
FEE, TAX = 0.00015, 0.0015


def setup_korean_font():
    if plt is None:
        return
    for path in ["/usr/share/fonts/truetype/nanum/NanumSquare_acR.ttf",
                 "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]:
        try:
            font_manager.fontManager.addfont(path)
            plt.rcParams["font.family"] = font_manager.FontProperties(
                fname=path).get_name()
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            pass


def load_data(years: int, bench_sym: str = "^KS11") -> tuple[pd.DataFrame, pd.Series]:
    end = datetime.today()
    start = end - timedelta(days=int(years * 365) + 60)
    import yfinance as yf
    px = yf.download(list(UNIVERSE), start=start, end=end,
                     interval="1d", progress=False, auto_adjust=True)["Close"]
    kospi = yf.download(bench_sym, start=start, end=end,
                        interval="1d", progress=False, auto_adjust=True)["Close"]
    if isinstance(kospi, pd.DataFrame):
        kospi = kospi.iloc[:, 0]
    px = px.dropna(how="all")
    px = px.dropna(axis=1, how="all")      # 조회 실패 종목 제외
    kospi = kospi.reindex(px.index).ffill()
    print(f"[데이터] 유니버스 {px.shape[1]}종목 × {len(px)}거래일 "
          f"({px.index[0].date()} ~ {px.index[-1].date()})")
    return px, kospi


def backtest(px: pd.DataFrame, kospi: pd.Series, cash: float, top_n: int,
             rebal: int, stop: float, weight: str = "equal",
             sector_map: dict | None = None, max_per_sector: int = 2) -> dict:
    ret = px.pct_change()
    r20 = px.pct_change(20)
    r60 = px.pct_change(60)
    score = 0.5 * r20 + 0.5 * r60
    trend_ok = px > px.rolling(100).mean()
    kospi_ok = kospi > kospi.rolling(200).mean()

    equity = np.full(len(px), np.nan)
    eq = cash
    qty = pd.Series(0.0, index=px.columns)   # 보유 수량
    avg = pd.Series(0.0, index=px.columns)   # 매입 단가
    entries = {}                              # code → 진입 자산(손익 계산용)
    trades, logs, rebalances = [], [], 0
    realized = wins = closes = 0
    peak, mdd = cash, 0.0

    for i, (dt, row) in enumerate(px.iterrows()):
        pxs = row
        # ---- 개별 손절 (매일 점검) ----
        for code in list(qty.index):
            if qty[code] > 0 and not math.isnan(pxs[code]):
                if pxs[code] <= avg[code] * (1 + stop):
                    gross = qty[code] * pxs[code]
                    pnl = gross - qty[code] * avg[code] - gross * (FEE + TAX) \
                          - qty[code] * avg[code] * FEE
                    realized += pnl
                    eq += gross * (1 - FEE - TAX)
                    wins += pnl > 0
                    closes += 1
                    nm = UNIVERSE.get(code, code)
                    logs.append((dt, f"손절 {nm} ({pnl:+,.0f}원)"))
                    trades.append({"date": dt, "code": code, "side": "손절",
                                   "pnl": pnl})
                    qty[code] = 0

        # ---- 리밸런스 ----
        if i >= 60 and (i - 60) % rebal == 0:
            rebalances += 1
            sc_raw = score.loc[dt].copy()
            ok = trend_ok.loc[dt].fillna(False) & (sc_raw.fillna(0) > 0)
            max_n = top_n if bool(kospi_ok.loc[dt]) else math.ceil(top_n / 2)
            if sector_map:
                sector_of = {c: s for s, cs in sector_map.items() for c in cs}
                heat = {s: float(r20.loc[dt, [c for c in cs if c in px.columns]].mean())
                        for s, cs in sector_map.items()
                        if any(c in px.columns for c in cs)}
                sc = sc_raw.copy()
                for c in sc.index:
                    sc[c] = sc[c] + 0.4 * heat.get(sector_of.get(c, ""), 0.0)
            else:
                sc = sc_raw
            ranked = sc[ok].sort_values(ascending=False).index.tolist()
            picks, cnt = [], {}
            for c in ranked:
                sec = sector_of.get(c, "?") if sector_map else "?"
                if sector_map and cnt.get(sec, 0) >= max_per_sector:
                    continue
                picks.append(c)
                cnt[sec] = cnt.get(sec, 0) + 1
                if len(picks) == max_n:
                    break
            nm = [UNIVERSE.get(c, c) for c in picks]
            logs.append((dt, f"리밸런스 #{rebalances}: 보유 {len(picks)}종목 "
                             f"[{', '.join(nm)}]"
                             f"{' · 방어모드' if max_n < top_n else ''}"))
            total_eq = eq + (qty * pxs).sum()
            # 1) 제외된 종목 전량 매도
            for code in list(qty.index):
                if qty[code] > 0 and code not in picks:
                    gross = qty[code] * pxs[code]
                    pnl = gross - qty[code] * avg[code] - gross * (FEE + TAX) \
                          - qty[code] * avg[code] * FEE
                    realized += pnl
                    eq += gross * (1 - FEE - TAX)
                    wins += pnl > 0
                    closes += 1
                    trades.append({"date": dt, "code": code, "side": "매도",
                                   "pnl": pnl})
                    qty[code] = 0
            # 2) 목표 비중 = 총자산 ÷ 보유 종목수 (총액 기반)
            if picks:
                w = (sc[picks] / sc[picks].sum()
                     if weight == "momentum" else
                     pd.Series(1 / len(picks), index=picks))
                for code in picks:
                    target = total_eq * float(w[code]) * (1 - FEE)
                    tgt_qty = target // pxs[code]
                    diff = tgt_qty - qty[code]
                    if diff > 0:
                        cost = diff * pxs[code] * (1 + FEE)
                        eq -= cost
                        avg[code] = (avg[code] * qty[code] +
                                     diff * pxs[code] * (1 + FEE)) / (qty[code] + diff)
                        qty[code] += diff
                    elif diff < 0:
                        gross = -diff * pxs[code]
                        pnl = gross * (1 - FEE - TAX) - (-diff) * avg[code]
                        realized += pnl
                        eq += gross * (1 - FEE - TAX)
                        wins += pnl > 0
                        closes += 1
                        trades.append({"date": dt, "code": code, "side": "매도",
                                       "pnl": pnl})
                        qty[code] = tgt_qty

        equity[i] = eq + (qty * pxs).sum()
        peak = max(peak, equity[i])
        mdd = min(mdd, equity[i] / peak - 1)

    equity = pd.Series(equity, index=px.index)
    final = float(equity.iloc[-1])
    days = (px.index[-1] - px.index[0]).days
    cagr = (final / cash) ** (365 / max(days, 1)) - 1
    r_eq = equity.pct_change().dropna()
    sharpe = (r_eq.mean() / r_eq.std() * math.sqrt(252)) if r_eq.std() > 0 else 0
    kospi_ret = float(kospi.iloc[-1] / kospi.iloc[0] - 1)
    equal_hold = float(px.ffill().iloc[-1].div(px.iloc[60]).mean() - 1)
    return {"final": final, "total_ret": final / cash - 1, "cagr": cagr,
            "mdd": mdd, "sharpe": sharpe, "kospi_ret": kospi_ret,
            "equal_hold": equal_hold, "rebalances": rebalances,
            "n_trades": len(trades),
            "win_rate": (wins / closes) if closes else float("nan"),
            "realized": realized, "equity": equity, "kospi": kospi,
            "logs": logs, "px": px}


def report(res: dict, cash: float, out_png: str, opts: str,
           bench_name: str = "코스피"):
    def won(x):
        return f"{x:,.0f}원" if x < 1e9 else f"{x / 1e8:,.2f}억원"

    print("\n" + "=" * 60)
    print(f"  포트폴리오 로테이션 백테스트  ({opts})")
    print("=" * 60)
    print(f"  최종 자산       : {won(res['final'])}  (시작 {won(cash)})")
    print(f"  총수익률        : {res['total_ret']:+.2%}")
    print(f"  {bench_name} 동기간   : {res['kospi_ret']:+.2%} / 유니버스 균등보유 {res['equal_hold']:+.2%}")
    print(f"  연평균(CAGR)    : {res['cagr']:+.2%} / 샤프 {res['sharpe']:.2f}")
    print(f"  최대낙폭(MDD)   : {res['mdd']:.2%}")
    print(f"  리밸런스 횟수   : {res['rebalances']}회 (매매 {res['n_trades']}건, "
          f"승률 {'-' if math.isnan(res['win_rate']) else format(res['win_rate'], '.1%')})")
    print("=" * 60)
    print("  최근 리밸런스 로그:")
    for dt, msg in res["logs"][-4:]:
        print(f"   · {dt.date()} {msg}")

    if plt is None:
        return
    setup_korean_font()
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(res["equity"].index, res["equity"] / cash, color="#059669",
            lw=1.5, label="로테이션 포트폴리오")
    ax.plot(res["kospi"].index, res["kospi"] / res["kospi"].iloc[0],
            color="#2563eb", lw=1.2, ls="--", label=bench_name)
    bh = res["px"].ffill().div(res["px"].iloc[60]).mean(axis=1)
    ax.plot(bh.index, bh, color="#9ca3af", lw=1.0, ls=":", label="유니버스 균등보유")
    ax.axhline(1, color="#d1d5db", lw=0.8)
    ax.set_title("모멘텀 로테이션 포트폴리오 백테스트 (총액 균등분할 · 실제 시세)",
                 fontsize=13, fontweight="bold")
    ax.set_ylabel("자산 지수 (시작=1)")
    ax.legend(fontsize=10)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print(f"[차트] {out_png} 저장 완료")


def main():
    global UNIVERSE, TAX
    ap = argparse.ArgumentParser(description="모멘텀 로테이션 백테스트")
    ap.add_argument("--years", type=int, default=3, help="기간(년)")
    ap.add_argument("--cash", type=float, default=10_000_000, help="시작 자금")
    ap.add_argument("--top", type=int, default=5, choices=range(4, 7),
                    help="동시 보유 종목 수 4~6")
    ap.add_argument("--rebal", type=int, default=20, help="리밸런스 주기(거래일)")
    ap.add_argument("--stop", type=float, default=-0.10, help="개별 손절율")
    ap.add_argument("--weight", default="equal", choices=["equal", "momentum"],
                    help="비중 방식: 균등 / 모멘텀가중")
    ap.add_argument("--us", action="store_true",
                    help="미국주식 유니버스 사용 (거래세 0, 벤치마크 S&P500)")
    ap.add_argument("--optimize", action="store_true",
                    help="파라미터 전수 탐색(그리드서치)으로 최적 조합을 찾아 적용")
    ap.add_argument("--out", default="backtest_portfolio.png")
    a = ap.parse_args()

    bench_name = "코스피"
    if a.us:
        UNIVERSE, TAX = US_UNIVERSE, 0.0
        bench_name = "S&P500"
        if a.out == "backtest_portfolio.png":
            a.out = "backtest_portfolio_us.png"

    px, kospi = load_data(a.years, "^GSPC" if a.us else "^KS11")

    if a.optimize:
        import itertools
        sector_map = US_SECTORS if a.us else None
        rows = []
        print("\n[최적화] 54가지 조합 × 실제 데이터 전수 시험 중… (1~2분)")
        for top_n, rd, st, wm in itertools.product([4, 5, 6], [5, 10, 15, 20],
                                                    [-0.08, -0.10, -0.12],
                                                    ["equal", "momentum"]):
            r = backtest(px, kospi, a.cash, top_n, rd, st, wm, sector_map=sector_map)
            rows.append({"top": top_n, "rebal": rd, "stop": st, "w": wm,
                         "ret": r["total_ret"], "mdd": r["mdd"],
                         "score": r["total_ret"] + r["mdd"]})
        rows.sort(key=lambda x: -x["score"])
        print("\n  ┌─ 최적화 상위 5조합 (위험조정 점수 = 수익률+낙폭) ─────────────")
        for i, r in enumerate(rows[:5], 1):
            print(f"  {i}위. 보유 {r['top']} · {r['rebal']}일 리밸런스 · 손절 {r['stop']:.0%} · "
                  f"{r['w']}가중 → 수익 {r['ret']:+.1%} / 낙폭 {r['mdd']:.1%}")
        print("  └──────────────────────────────────────────────────────────────")
        best = rows[0]
        a.top, a.rebal, a.stop, a.weight = best["top"], best["rebal"], best["stop"], best["w"]
        print(f"\n[적용] 1위 조합으로 최종 리포트를 다시 계산합니다: "
              f"보유 {a.top} · {a.rebal}일 · 손절 {a.stop:.0%} · {a.weight}가중\n")

    res = backtest(px, kospi, a.cash, a.top, a.rebal, a.stop, a.weight,
                   sector_map=(US_SECTORS if a.us else None))
    opts = (f"보유 {a.top}종목 · {a.rebal}거래일 리밸런스 · {a.weight}가중 · "
            f"손절 {a.stop:.0%}" + (" · 미국주식" if a.us else ""))
    report(res, a.cash, a.out, opts, bench_name)


if __name__ == "__main__":
    main()
