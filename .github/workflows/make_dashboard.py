#!/usr/bin/env python3
"""state.json + trades.csv + settings.json → 웹 대시보드(index.html) 생성
   GitHub Pages에 배포되며, 페이지의 제어 패널은 settings.json을 갱신합니다."""
import csv, json, os, html
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
START_CASH = 10000000
DEFAULTS = {"enabled": True, "top_n": 4, "rebal_min": 20, "stop": -0.08, "take": 0.08, "trail": 0.05}

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

cfg = dict(DEFAULTS)
try:
    cfg.update(json.loads(load(os.path.join(HERE, "settings.json"), "{}")))
except ValueError:
    pass
TOP = int(cfg.get("top_n") or 4)

for c, v in st.get("day_closes", {}).items():
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
rows_pos = rows_pos or f"<tr><td colspan='6'>보유 종목 없음 — 다음 리밸런스에서 편입 시도</td></tr>"

rows_tr = ""
for r in load(os.path.join(HERE, "trades.csv"), "").strip().splitlines()[-20:][::-1]:
    f = list(csv.reader([r]))[0] if r else []
    if len(f) >= 6 and f[0] != "시각":
        rows_tr += ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                    % tuple(esc(x) for x in f[:2] + [f"{f[2]} {NAME.get(f[2], f[2])}"] + f[3:6]))
rows_tr = rows_tr or "<tr><td colspan='6'>아직 기록 없음</td></tr>"

def kpi(k, v): return f"<div class='kpi'><div class='k'>{k}</div><div class='kv'>{v}</div></div>"

badge_col = "#2FBF71" if cfg.get("enabled", True) else "#E5484D"
badge_txt = "실행중" if cfg.get("enabled", True) else "정지됨"
opts = lambda v, cur: "".join(f"<option {'selected' if str(x)==str(cur) else ''} value='{x}'>{x}</option>"
                              for x in v)
now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

CONTROL = """
<div class="card" id="ctrl">
 <h3 style="margin:0 0 6px;font-size:13px;color:#F5B942">제어 패널</h3>
 <div id="badge" style="font-size:14px;font-weight:700;color:__BC__">봇 상태: __BT__ <span class="k">(페이지를 연 시점 기준 · 아래 버튼으로 변경)</span></div>
 <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">
  <button onclick="setEn(true)"  class="bt" style="background:#2FBF71">▶ 시작</button>
  <button onclick="setEn(false)" class="bt" style="background:#E5484D">■ 정지</button>
  <button onclick="saveCfg()"    class="bt" style="background:#F5B942;color:#0E1216">설정 저장</button>
  <button onclick="runNow()"     class="bt" style="background:#3E76D6">지금 리밸런스</button>
 </div>
 <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:8px;font-size:12px">
  <label>보유 종목 수 <select id="c_top" class="in">__OPT_TOP__</select></label>
  <label>리밸런스 주기(분) <select id="c_rebal" class="in">__OPT_REB__</select></label>
  <label>손절 % <input id="c_stop" type="number" step="1" class="in" style="width:64px" value="__V_STOP__"></label>
  <label>익절 % <input id="c_take" type="number" step="1" class="in" style="width:64px" value="__V_TAKE__"></label>
  <label>트레일링 % <input id="c_trail" type="number" step="1" class="in" style="width:64px" value="__V_TRAIL__"></label>
 </div>
 <div style="margin-top:8px;font-size:12px">
  <input id="tok" class="in" style="width:270px" placeholder="GitHub 토큰 — 한 번만 넣으면 저장됨">
  <button onclick="saveTok()" class="bt" style="background:#29323E">토큰 저장</button>
 </div>
 <div id="msg" style="margin-top:6px;font-size:12px;color:#F5B942"></div>
 <div class="k" style="margin-top:6px">변경은 <b>다음 실행(최대 20분 내)부터 적용</b>됩니다. 토큰: GitHub → Settings → Developer settings →
 Fine-grained tokens → 이 저장소만 지정, 권한 Contents: Read and write(시작/정지·설정) + Actions: Read and write(지금 리밸런스).
 토큰은 이 브라우저에만 저장되며 언제든 폐기할 수 있습니다.</div>
</div>
<script>
var OWNER = location.hostname.split(".")[0], REPO = (location.pathname.split("/")[1] || "");
function b64e(s){ return btoa(unescape(encodeURIComponent(s))); }
function b64d(s){ return decodeURIComponent(escape(atob(s.replace(/\\n/g, "")))); }
function hdr(){ var t = localStorage.getItem("gh_pat") || "";
  var h = { "Accept": "application/vnd.github+json" };
  if (t) h["Authorization"] = "Bearer " + t;
  return h; }
function msg(m){ document.getElementById("msg").textContent = m; }
function badge(on){ var b = document.getElementById("badge");
  b.innerHTML = "봇 상태: " + (on ? "실행중" : "정지됨") + ' <span class="k">(변경 시 다음 실행부터 적용)</span>';
  b.style.color = on ? "#2FBF71" : "#E5484D"; }
async function getCfg(){
  var r = await fetch("https://api.github.com/repos/" + OWNER + "/" + REPO + "/contents/settings.json", { headers: hdr() });
  if (r.status === 404) return { sha: null, cfg: {} };
  if (!r.ok) throw new Error("HTTP " + r.status);
  var j = await r.json();
  return { sha: j.sha, cfg: JSON.parse(b64d(j.content)) };
}
async function putCfg(cfg, sha){
  var r = await fetch("https://api.github.com/repos/" + OWNER + "/" + REPO + "/contents/settings.json", {
    method: "PUT", headers: Object.assign({ "Content-Type": "application/json" }, hdr()),
    body: JSON.stringify({ message: "대시보드에서 설정 변경", content: b64e(JSON.stringify(cfg, null, 2)), sha: sha, branch: "main" }) });
  if (!r.ok) throw new Error(r.status === 403 ? "권한 없음 — 토큰의 Contents 권한 확인" : "HTTP " + r.status);
}
async function updateCfg(mut){
  for (var i = 0; i < 3; i++){
    var g = await getCfg();
    try { var next = mut(g.cfg); await putCfg(next, g.sha); return next; }
    catch (e) { if (String(e.message).indexOf("409") < 0) throw e; }
  }
}
function fromInputs(){
  return { enabled: null, top_n: +document.getElementById("c_top").value,
           rebal_min: +document.getElementById("c_rebal").value,
           stop: -Math.abs(+document.getElementById("c_stop").value) / 100,
           take: Math.abs(+document.getElementById("c_take").value) / 100,
           trail: Math.abs(+document.getElementById("c_trail").value) / 100 };
}
function fill(c){
  if (c.top_n) document.getElementById("c_top").value = c.top_n;
  if (c.rebal_min) document.getElementById("c_rebal").value = c.rebal_min;
  if (c.stop != null) document.getElementById("c_stop").value = Math.round(Math.abs(c.stop) * 100);
  if (c.take != null) document.getElementById("c_take").value = Math.round(c.take * 100);
  if (c.trail != null) document.getElementById("c_trail").value = Math.round(c.trail * 100);
}
async function setEn(on){
  try { await updateCfg(function(c){ c.enabled = on; return c; });
        badge(on); msg(on ? "시작 요청 완료 — 다음 실행부터 매매 재개" : "정지 요청 완료 — 다음 실행부터 매매 안 함"); }
  catch (e) { msg("실패: " + e.message); }
}
async function saveCfg(){
  try { var c = await updateCfg(function(c0){ var n = fromInputs(); n.enabled = c0.enabled !== false; return n; });
        fill(c); msg("설정 저장 완료 — 다음 실행부터 적용"); }
  catch (e) { msg("실패: " + e.message); }
}
async function runNow(){
  try { var r = await fetch("https://api.github.com/repos/" + OWNER + "/" + REPO +
        "/actions/workflows/bot.yml/dispatches", { method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" }, hdr()),
        body: JSON.stringify({ ref: "main" }) });
    if (!r.ok) throw new Error(r.status === 403 ? "토큰에 Actions 권한 필요" : "HTTP " + r.status);
    msg("실행 요청 완료 — Actions 탭에서 진행 확인");
  } catch (e) { msg("실패: " + e.message); }
}
function saveTok(){ localStorage.setItem("gh_pat", document.getElementById("tok").value.trim()); msg("토큰 저장 완료"); }
if (!REPO) { document.getElementById("ctrl").style.display = "none"; }
else { (async function(){ try { var g = await getCfg(); fill(g.cfg); badge(g.cfg.enabled !== false); }
        catch (e) { msg("설정 읽기 실패: " + e.message); } })(); }
</script>
"""

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
.cards{{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}}
.in{{background:#1D2530;color:#E9EDF3;border:1px solid #29323E;border-radius:6px;padding:4px 6px}}
.bt{{color:#fff;border:0;border-radius:7px;padding:8px 14px;font-weight:700;cursor:pointer;font-size:13px}}</style><div class="wrap">
<h2 style="margin-top:0">로테이션 봇 대시보드</h2>
<div class="k">마지막 실행: {now} · 20분마다 자동 실행 · 미국 정규장(뉴욕 09:30~16:00)에만 매매 · <a href="#" onclick="location.reload();return false" style="color:#7FB2F0">새로고침</a></div>
<div class="big">${fmt(total)}</div><div class="k">수익률 {'+' if ret >= 0 else ''}{ret*100:.1f}% · 시작자본 ${fmt(START_CASH)}</div>
<div class="cards">{kpi('현금', '$' + fmt(st['cash']))}{kpi('투자중', fmt(inv / total * 100 if total else 0) + '%')}{kpi('평가손익', '$' + fmt(upl))}{kpi('실현손익', '$' + fmt(st['realized']))}{kpi('리밸런스', str(st['rebal_count']) + '회')}{kpi('매매', str(st['trades']) + '건')}{kpi('승률', (str(round(st['wins'] / st['closes'] * 100)) + '%') if st['closes'] else '0%')}</div>
{CONTROL.replace('__BC__', badge_col).replace('__BT__', badge_txt).replace('__OPT_TOP__', opts(range(2, 9), TOP)).replace('__OPT_REB__', opts([10, 15, 20, 30, 60], cfg.get('rebal_min') or 20)).replace('__V_STOP__', str(round(abs(cfg.get('stop') or -0.08) * 100))).replace('__V_TAKE__', str(round((cfg.get('take') or 0.08) * 100))).replace('__V_TRAIL__', str(round((cfg.get('trail') or 0.05) * 100)))}
<h2>보유 포트폴리오 ({len(st['pos'])}/{TOP})</h2><div class="card"><table><tr><th>종목</th><th>섹터</th><th>수량</th><th>평단$</th><th>현재가</th><th>손익</th></tr>{rows_pos}</table></div>
<h2>최근 매매</h2><div class="card"><table><tr><th>시각</th><th>구분</th><th>종목</th><th>수량</th><th>가격</th><th>사유</th></tr>{rows_tr}</table></div>
<div class="k" style="margin:14px 0 30px">모의 계좌 기록입니다 · 열람 전용 (제어는 위 패널 또는 settings.json)</div></div>"""

with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
    f.write(page)
print(f"[대시보드] index.html 생성 — 총자산 ${total:,.0f} · 보유 {len(st['pos'])}종목 · 상태 {'실행중' if cfg.get('enabled', True) else '정지됨'}")
