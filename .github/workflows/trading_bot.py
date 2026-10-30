# -*- coding: utf-8 -*-
"""
가상(모의) 주식 자동매매 엔진 + 백테스트
========================================
전략: 추세 추종(이동평균 크로스) + RSI 평균회귀 하이브리드
  - 진입: 골든크로스 발생, 또는 상승 추세에서 RSI 과매도(35 이하) 반등 시 매수
  - 청산: 데드크로스, RSI 과열(75 이상), 또는 손절(-7%) 도달 시 매도
  - 포지션: 현금의 95%를 1회 진입 (수수료/거래세 반영)

실행 예시:
  python3 trading_bot.py                          # 삼성전자 3년 백테스트
  python3 trading_bot.py --ticker 000660.KS      # SK하이닉스
  python3 trading_bot.py --ticker AAPL --years 5 # 미국주식(거래세 0 자동)

실제 계좌 연동은 README.md의 '실전 연동 가이드'를 참고하세요.
본 코드는 교육/모의투자 목적이며 수익을 보장하지 않습니다.
"""

import argparse
import math
import sys
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

# ---------------------------------------------------------------- 한글 폰트
def setup_korean_font():
    if plt is None:
        return None
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumSquare_acR.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            font_manager.fontManager.addfont(path)
            name = font_manager.FontProperties(fname=path).get_name()
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return name
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- 데이터
def fetch_data(ticker: str, years: int) -> pd.DataFrame:
    """실제 시세(yfinance) → 실패 시 가상 데이터(기하브라운운동)로 대체."""
    end = datetime.today()
    start = end - timedelta(days=int(years * 365) + 30)
    try:
        import yfinance as yf
        df = yf.download(ticker, start=start, end=end,
                         interval="1d", progress=False, auto_adjust=True)
        if df is not None and len(df) > 100:
            if isinstance(df.columns, pd.MultiIndex):        # yfinance 티커 멀티컬럼 정리
                df.columns = [c[0] for c in df.columns]
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            df.index.name = "Date"
            print(f"[데이터] {ticker} 실제 시세 {len(df)}건 "
                  f"({df.index[0].date()} ~ {df.index[-1].date()})")
            return df
    except Exception as e:
        print(f"[데이터] 실제 시세 조회 실패({e}) → 가상 데이터로 대체합니다.")

    # --- 가상 데이터: 추세/횡보 구간이 번갈아 나타나는 시뮬레이션 ---
    rng = np.random.default_rng(42)
    n = int(years * 252)
    dates = pd.bdate_range(end=end, periods=n)
    price, drift, vol = 50_000.0, 0.0002, 0.018
    rets = []
    for i in range(n):
        if i % 60 == 0:                                   # 60거래일마다 체제 전환
            drift = rng.choice([-0.0008, 0.0002, 0.0006])
            vol = rng.choice([0.012, 0.018, 0.028])
        rets.append(drift + vol * rng.standard_normal())
    close = price * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.006, n)))
    vol_col = rng.integers(1e6, 2e7, n)
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low,
                       "Close": close, "Volume": vol_col}, index=dates)
    print(f"[데이터] {ticker} 가상 시세 {n}건 생성 (실시세 조회 실패 시 학습용)")
    return df


# ---------------------------------------------------------------- 지표
def add_indicators(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    out = df.copy()
    out["SMA_F"] = out["Close"].rolling(fast).mean()
    out["SMA_S"] = out["Close"].rolling(slow).mean()
    delta = out["Close"].diff()
    up = delta.clip(lower=0).rolling(14).mean()
    dn = (-delta.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, np.nan)
    out["RSI"] = 100 - 100 / (1 + rs)
    tr = pd.concat([out["High"] - out["Low"],
                    (out["High"] - out["Close"].shift()).abs(),
                    (out["Low"] - out["Close"].shift()).abs()], axis=1).max(axis=1)
    out["ATR"] = tr.rolling(14).mean()
    return out


# ---------------------------------------------------------------- 백테스트
def backtest(df: pd.DataFrame, cash: float, fee: float, tax: float,
             stop_loss: float = -0.07, rsi_oversold: float = 35.0,
             rsi_overbought: float = 75.0) -> dict:
    rows = df.dropna(subset=["SMA_F", "SMA_S", "RSI"])
    equity = np.full(len(df), np.nan)
    eq = cash
    shares, avg_price, prev_cross = 0, 0.0, 0
    trades, logs = [], []
    entry_cost = 0.0  # 진입 시 총 매입가(수수료 포함)
    peak, mdd = cash, 0.0

    for i, (dt, r) in enumerate(rows.iterrows()):
        px = float(r["Close"])
        cross = 1 if r["SMA_F"] > r["SMA_S"] else (-1 if r["SMA_F"] < r["SMA_S"] else 0)
        rsi = float(r["RSI"])
        reason = None

        # ---- 매도 판단 ----
        if shares > 0:
            ret_pos = px / avg_price - 1
            if prev_cross == 1 and cross == -1:
                reason = "데드크로스"
            elif rsi > rsi_overbought:
                reason = f"RSI 과열({rsi:.0f})"
            elif ret_pos <= stop_loss:
                reason = f"손절({ret_pos:+.1%})"
            if reason:
                gross = shares * px
                cost = gross * (fee + tax)
                eq += gross - cost
                trades.append({"date": dt, "side": "SELL", "price": px,
                               "shares": shares, "pnl": gross - cost - entry_cost,
                               "reason": reason})
                logs.append((dt, f"매도 {shares:,}주 @ {px:,.0f} ({reason})"))
                shares, entry_cost = 0, 0.0

        # ---- 매수 판단 ----
        if shares == 0:
            golden = prev_cross == -1 and cross == 1
            dip_buy = cross == 1 and rsi < rsi_oversold
            if golden or dip_buy:
                budget = eq * 0.95
                qty = int(budget // px / 10) * 10 or int(budget // px)  # 소수점 없이
                if qty > 0:
                    cost = qty * px * (1 + fee)
                    eq -= cost
                    shares, avg_price = qty, px
                    entry_cost = cost
                    sig = "골든크로스" if golden else f"RSI 반등({rsi:.0f})"
                    trades.append({"date": dt, "side": "BUY", "price": px,
                                   "shares": qty, "pnl": 0.0, "reason": sig})
                    logs.append((dt, f"매수 {qty:,}주 @ {px:,.0f} ({sig})"))

        prev_cross = cross if cross else prev_cross
        equity[i] = eq + shares * px
        peak = max(peak, equity[i])
        mdd = min(mdd, equity[i] / peak - 1)

    equity = pd.Series(equity, index=df.index).ffill().fillna(cash)
    final = float(equity.iloc[-1])
    days = (df.index[-1] - df.index[0]).days
    cagr = (final / cash) ** (365 / max(days, 1)) - 1
    ret_eq = equity.pct_change().dropna()
    sharpe = (ret_eq.mean() / ret_eq.std() * math.sqrt(252)) if ret_eq.std() > 0 else 0
    bh = df["Close"].iloc[-1] / df["Close"].iloc[0] - 1

    closed = [t for t in trades if t["side"] == "SELL"]
    wins = sum(1 for t in closed if t["pnl"] > 0)
    return {
        "final": final, "total_ret": final / cash - 1, "cagr": cagr,
        "mdd": mdd, "sharpe": sharpe, "n_trades": len(trades),
        "win_rate": (wins / len(closed)) if closed else float("nan"),
        "buy_hold": bh, "equity": equity, "trades": trades, "logs": logs,
        "df": df,
    }


# ---------------------------------------------------------------- 리포트/차트
def report(ticker: str, res: dict, cash: float, out_png: str):
    def won(x):
        return f"{x:,.0f}원" if x < 1e9 else f"{x / 1e8:,.1f}억원"

    print("\n" + "=" * 58)
    print(f"  백테스트 결과 — {ticker} ({len(res['df'])}거래일)")
    print("=" * 58)
    print(f"  최종 자산     : {won(res['final'])}  (시작 {won(cash)})")
    print(f"  총수익률      : {res['total_ret']:+.2%}   (단순 보유 {res['buy_hold']:+.2%})")
    print(f"  연평균(CAGR)  : {res['cagr']:+.2%}")
    print(f"  최대낙폭(MDD) : {res['mdd']:.2%}")
    print(f"  샤프지수      : {res['sharpe']:.2f}")
    print(f"  매매횟수/승률 : {res['n_trades']}회 / "
          f"{'-' if math.isnan(res['win_rate']) else format(res['win_rate'], '.1%')}")
    print("=" * 58)

    if plt is None:
        print("[차트] matplotlib 미설설치로 차트를 건너뜁니다.")
        return

    setup_korean_font()
    df, eq = res["df"], res["equity"]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    ax1.plot(df.index, df["Close"], color="#1f2937", lw=1.1, label="종가")
    ax1.plot(df.index, df["SMA_F"], color="#f59e0b", lw=0.9, label="단기 이평")
    ax1.plot(df.index, df["SMA_S"], color="#3b82f6", lw=0.9, label="장기 이평")
    buys = [t for t in res["trades"] if t["side"] == "BUY"]
    sells = [t for t in res["trades"] if t["side"] == "SELL"]
    ax1.scatter([t["date"] for t in buys], [t["price"] for t in buys],
                marker="^", s=64, color="#e11d48", zorder=5, label="매수")
    ax1.scatter([t["date"] for t in sells], [t["price"] for t in sells],
                marker="v", s=64, color="#2563eb", zorder=5, label="매도")
    ax1.set_title(f"{ticker} 자동매매 백테스트 (전략: 이평 크로스 + RSI)",
                  fontsize=13, fontweight="bold")
    ax1.legend(loc="upper left", fontsize=9, ncol=3)
    ax1.grid(alpha=0.25)

    ax2.plot(eq.index, eq / cash, color="#059669", lw=1.3, label="전략 자산")
    ax2.plot(df.index, df["Close"] / df["Close"].iloc[0], color="#9ca3af",
             lw=1.0, ls="--", label="단순 보유")
    ax2.axhline(1, color="#d1d5db", lw=0.8)
    ax2.set_ylabel("자산 지수 (시작=1)")
    ax2.legend(loc="upper left", fontsize=9)
    ax2.grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_png, dpi=130)
    print(f"[차트] {out_png} 저장 완료")


# ---------------------------------------------------------------- 실행
def main():
    ap = argparse.ArgumentParser(description="가상 주식 자동매매 백테스트")
    ap.add_argument("--ticker", default="005930.KS", help="종목 티커 (예: 005930.KS, AAPL)")
    ap.add_argument("--years", type=float, default=3, help="백테스트 기간(년)")
    ap.add_argument("--cash", type=float, default=10_000_000, help="시작 자금(원)")
    ap.add_argument("--fast", type=int, default=20, help="단기 이평 기간")
    ap.add_argument("--slow", type=int, default=60, help="장기 이평 기간")
    ap.add_argument("--fee", type=float, default=0.00015, help="매매 수수료율")
    ap.add_argument("--tax", type=float, default=None, help="매도 거래세율(기본: 미국주식 0, 국내 0.0015)")
    ap.add_argument("--out", default="backtest_result.png", help="차트 저장 파일명")
    args = ap.parse_args()

    is_us = "." not in args.ticker or args.ticker.upper().endswith((".US", ".TO"))
    tax = args.tax if args.tax is not None else (0.0 if is_us else 0.0015)

    df = fetch_data(args.ticker, args.years)
    df = add_indicators(df, args.fast, args.slow)
    res = backtest(df, args.cash, args.fee, tax)
    report(args.ticker, res, args.cash, args.out)


if __name__ == "__main__":
    main()
