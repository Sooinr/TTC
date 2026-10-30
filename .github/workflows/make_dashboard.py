#!/usr/bin/env python3
"""state.json + trades.csv 를 읽어 index.html 대시보드를 생성 (GitHub Pages 배포용)"""
import csv, json, os, html
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
START_CASH = 10000000

def load(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default

assets = json.loads(load(os.path.join(HERE, "universe_us.json"), "[]"))
NAME = {a["code"]: a.get("name", a["code"]) for a in assets}
SEC = {a["code"]: a.get("sector", "") for a in assets}
PX = {a["code"]: a["hist"][-1] for a in assets if a.get("hist")}

st = {}
try:
    st = json.loads(load(os.path.join(HERE, "state.json"), "{}"))
except ValueError:
    pass
if not st:
    st = {"cash": START_CASH, "pos": {}, "realized": 0, "wins": 0, "closes": 0,
          "trades": 0, "rebal_count": 0, "last_rebal": 0, "day_closes": {}}

for c, v in st.get("day_closes", {}).items():   # 최근 종가를 평가 기준가로
    if v: PX[c] = v[-1]

inv = sum(p["qty"] * PX[c] for c, p in st["pos"].items() if c in PX)
total = st["cash"] + inv
ret = total / START_CASH - 1
upl = sum(p["qty"] * (PX[c] - p["avg"]) for c, p in st["pos"].items() if c in PX)

def esc(s): return html.escape(str(s))
def fmt(n): return f"{n:,.0f}"

rows_pos = ""
for c, p in st["pos"].items():
    px = PX.get(c, 0)
    r = (px / p["avg"] - 1) * 100 if px else 0
    col = "#2FBF71" if r >= 0 else "#E5484D"
    arrow = "▲ +" if r >= 0 else "▼ "
    rows_pos += (f"<tr><td><b>{c}</b> {esc(NAME.get(c,c))}</td><td>{esc(SEC.get(c,''))}</td>"
                 f"<td>{fmt(p['qty'])}</td><td>{p['avg']:.2f}</td><td>{px:.2f}</td>"
                 f"<td style='color:{col}'>{arrow}{r:.1f}%</td></tr>")
rows_pos = rows_pos or "<tr><td colspan='6'>보유 종목 없음 — 다음 리밸런스에서 편입 시도</td></tr>"

rows_tr = ""
lines = load(os.path.join(HERE, "trades.csv"), "").strip().splitlines()
for r in lines[-20:][::-1]:
    f = list(csv.reader([r]))[0] if r else []
    if len(f) >= 6 and f[0] != "시각":
        rows_tr += ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % tuple(esc(x) for x in f[:2] + [f"{f[2]} {NAME.get(f[2],f[2])}"] + f[3:6]))
rows_tr = rows_tr or "<tr><td colspan='6'>아직 기록 없음</td></tr>"

def kpi(k, v): return f"<div class='kpi'><div class='k'>{k}</div><div class='kv'>{v}</div></div>"
now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

page = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>로테이션 봇 대시보드</title>
<style>body{{font-family:sans-serif;background:#0E1216;color:#E9EDF3;padding:14px;margin:0}}
.wrap{{max-width:900px;margin:0 auto}}
.k{{color:#93A0B0;font-size:11px}} h2{{font-size:14px;color:#F5B942;margin:16px 0 2px}}
.big{{font-size:32px;font-weight:700;color:{'#2FBF71' if ret >= 0 else '#E5484D'};margin:2px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}}
td,th{{padding:5px 6px;border-bottom:1px solid #29323E;text-align:left}}
.kpi{{flex:1;min-width:120px;background:#161C24;border:1px solid #29323E;border-radius:8px;padding:8px 10px}}
.kv{{font-size:15px;font-weight:700;margin-top:2px}}
.card{{background:#161C24;border:1px solid #29323E;border-radius:10px;padding:10px;margin-top:8px}}
.cards{{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}}</style><div class="wrap">
<h2 style="margin-top:0">로테이션 봇 대시보드</h2>
<div class="k">마지막 실행: {now} · 20분마다 자동 갱신 · 미국 정규장(뉴욕 09:30~16:00)에만 매매</div>
<div class="big">${fmt(total)}</div><div class="k">수익률 {'+' if ret>=0 else ''}{ret*100:.1f}% · 시작자본 ${fmt(START_CASH)}</div>
<div class="cards">{kpi('현금','$'+fmt(st['cash']))}{kpi('투자중', fmt(inv/total*100 if total else 0)+'%')}{kpi('평가손익','$'+fmt(upl))}{kpi('실현손익','$'+fmt(st['realized']))}{kpi('리밸런스', str(st['rebal_count'])+'회')}{kpi('매매', str(st['trades'])+'건')}{kpi('승률', (str(round(st['wins']/st['closes']*100))+'%') if st['closes'] else '0%')}</div>
<h2>보유 포트폴리오 ({len(st['pos'])}/4)</h2><div class="card"><table><tr><th>종목</th><th>섹터</th><th>수량</th><th>평단$</th><th>현재가</th><th>손익</th></tr>{rows_pos}</table></div>
<h2>최근 매매</h2><div class="card"><table><tr><th>시각</th><th>구분</th><th>종목</th><th>수량</th><th>가격</th><th>사유</th></tr>{rows_tr}</table></div>
<div class="k" style="margin:14px 0 30px">모의 계좌 기록입니다 · 열람 전용</div></div>"""

with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
    f.write(page)
print(f"[대시보드] index.html 생성 — 총자산 ${total:,.0f} · 보유 {len(st['pos'])}종목")
