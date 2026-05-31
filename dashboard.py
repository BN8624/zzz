"""
AI Auto Coder — 대시보드 (아이폰 우선 / 타이쿤 감성)
단일 파일: Flask 백엔드 + HTML/CSS/JS 인라인

기능
- 비밀번호 인증 (.env: DASHBOARD_PASSWORD)
- 탭 3개: 현황 / ⭐즐겨찾기 / 생산로그
- 즐겨찾기 지정·해제·메모수정·코드보기·코드복사
- 아이폰 하단 탭, 홈화면 추가 대응, http 복사 폴백

실행:  python dashboard.py
접속:  http://<VM외부IP>:5000
"""

import os
import re
import sqlite3
import secrets
from functools import wraps
from datetime import datetime, date

from flask import (
    Flask, request, session, redirect,
    jsonify, Response,
)
from dotenv import load_dotenv

from config import DB_PATH
from agents import get_usage   # 마지막 모델 호출 시각(가동등 판단)용

load_dotenv()

# ── 설정 ─────────────────────────────────────────────
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "changeme")
PORT = 5000

# 생산로그 원본 파일. nohup 으로 돌릴 때 만든 output.log 를 그대로 읽습니다.
#  (main.py 의 화면 로그가 전부 여기로 들어옴 → SSH 에서 보던 것과 동일)
#  파일이 너무 크면 끝부분만 읽습니다(아래 LOG_TAIL_BYTES).
OUTPUT_LOG = "output.log"
LOG_TAIL_BYTES = 200_000   # 마지막 약 200KB 만 읽기(폰 전송량·속도 보호)
LOG_MAX_CYCLES = 30        # 화면에 보여줄 최근 작업 사이클 최대 개수

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET", secrets.token_hex(16))


# ── DB 헬퍼 (대시보드 전용, code_database 건드리지 않음) ──
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_status():
    """현황 탭 데이터"""
    conn = db_conn()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM results")
    total = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM results WHERE is_favorite = 1")
    favorites = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM results WHERE human_reviewed_at IS NULL")
    unreviewed = c.fetchone()[0]

    c.execute("SELECT AVG(ai_score) FROM results")
    avg_score = c.fetchone()[0] or 0

    # 오늘 생산(저장)된 수
    today = date.today().isoformat()
    c.execute("SELECT COUNT(*) FROM results WHERE created_at LIKE ?", (today + "%",))
    today_saved = c.fetchone()[0]

    conn.close()

    # 가동 판단: DB '저장' 시각이 아니라 '마지막 모델 호출' 시각 기준.
    #  4점 미만은 저장이 안 돼서, 저장 기준이면 멀쩡히 돌아도 빨간불이 떴음.
    #  모델 호출은 매 작업마다 일어나므로, 이게 "살아있다"의 정확한 신호.
    #  (자동루프와 대시보드가 같은 프로세스라 agents의 사용량을 그대로 읽음)
    last_call = get_usage().get("last_call_at")
    running = False
    last_label = "기록 없음"
    if last_call:
        try:
            dt = datetime.fromisoformat(last_call)
            diff = (datetime.now() - dt).total_seconds()
            running = diff < 600  # 최근 10분 내 호출이 있으면 가동중
            last_label = dt.strftime("%H:%M")
        except ValueError:
            pass

    return {
        "running": running,
        "last_label": last_label,
        "today_saved": today_saved,
        "total": total,
        "favorites": favorites,
        "unreviewed": unreviewed,
        "avg_score": round(avg_score, 2),
    }


def get_logs(limit=50):
    """생산로그 탭: 시:분 + 토픽 + 점수 + 즐겨찾기 여부"""
    conn = db_conn()
    c = conn.cursor()
    c.execute(
        """SELECT id, topic, ai_score, is_favorite, created_at
           FROM results ORDER BY created_at DESC LIMIT ?""",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()

    out = []
    for r in rows:
        t = ""
        try:
            t = datetime.fromisoformat(r["created_at"]).strftime("%H:%M")
        except (ValueError, TypeError):
            pass
        out.append({
            "id": r["id"],
            "time": t,
            "topic": r["topic"],
            "score": round(r["ai_score"], 1) if r["ai_score"] else 0,
            "is_favorite": bool(r["is_favorite"]),
        })
    return out


# ── 생산로그(output.log) 사이클 단위 파싱 ──────────────
#  output.log 를 읽어, "작업 #" 시작 줄을 경계로 한 사이클씩 잘라
#  목록(헤더)과 본문(전체 로그)을 만들어 돌려줍니다.
#  SSH 에서 보던 그 로그를, 폰에서 작업별로 접었다 폈다 보기 위함입니다.

# 작업 시작을 알리는 줄에 들어있는 표식. (logger.py 의 log_task_start 박스)
_CYCLE_MARK = "AI AUTO CODER"


def _read_log_tail():
    """output.log 의 끝부분만 읽어 텍스트로 돌려줍니다(없으면 빈 문자열).
       파일이 크면 통째로 읽지 않고 마지막 LOG_TAIL_BYTES 만 읽습니다."""
    try:
        size = os.path.getsize(OUTPUT_LOG)
        with open(OUTPUT_LOG, "r", encoding="utf-8", errors="replace") as f:
            # 파일이 크면 끝에서 LOG_TAIL_BYTES 만큼 앞으로 가서 읽기 시작.
            if size > LOG_TAIL_BYTES:
                f.seek(size - LOG_TAIL_BYTES)
                f.readline()   # 중간에서 잘린 첫 줄은 버림(깨진 줄 방지)
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def get_log_cycles():
    """로그를 사이클(작업 1건) 단위로 잘라 최신순 목록으로 돌려줍니다.
       각 항목: {idx, title, score, discarded, body}
         - title    : 작업 첫 줄(주제 일부)
         - score    : 평가 점수(있으면), 없으면 None
         - discarded: '폐기' 표시가 있으면 True
         - body     : 그 사이클의 전체 로그 텍스트(SSH 화면 그대로)"""
    text = _read_log_tail()
    if not text:
        return []

    lines = text.splitlines()

    # 1) "작업 #" 박스가 나오는 줄 위치를 모두 찾습니다(사이클 시작점).
    starts = [i for i, ln in enumerate(lines) if _CYCLE_MARK in ln]
    if not starts:
        return []

    # 2) 시작점들로 구간을 잘라 사이클 본문을 만듭니다.
    cycles = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        body_lines = lines[s:e]
        body = "\n".join(body_lines)

        # 제목: 박스 다음 줄들 중 글자가 있는 첫 줄을 주제로 씁니다.
        title = ""
        for ln in body_lines[1:]:
            cleaned = ln.strip().strip("│").strip()
            if cleaned and "─" not in cleaned and "AI AUTO CODER" not in cleaned:
                title = cleaned
                break

        # 점수: 본문에서 "x.x/5.0" 패턴을 찾습니다.
        score = None
        m = re.search(r"(\d\.\d)\s*/\s*5\.0", body)
        if m:
            try:
                score = float(m.group(1))
            except ValueError:
                pass

        # 폐기 여부: "폐기" 글자가 있으면 폐기된 작업.
        discarded = "폐기" in body

        cycles.append({
            "idx": n,
            "title": title[:60] if title else "(제목 없음)",
            "score": score,
            "discarded": discarded,
            "body": body,
        })

    # 3) 최신이 위로 오게 뒤집고, 최근 N개만.
    cycles.reverse()
    return cycles[:LOG_MAX_CYCLES]


def get_favorites():
    """즐겨찾기 탭"""
    conn = db_conn()
    c = conn.cursor()
    c.execute(
        """SELECT id, topic, ai_score, human_note, code
           FROM results WHERE is_favorite = 1
           ORDER BY ai_score DESC""",
    )
    rows = c.fetchall()
    conn.close()

    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "topic": r["topic"],
            "score": round(r["ai_score"], 1) if r["ai_score"] else 0,
            "note": r["human_note"] or r["topic"],
            "code": r["code"] or "",
        })
    return out


def get_code(result_id):
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT code FROM results WHERE id = ?", (result_id,))
    row = c.fetchone()
    conn.close()
    return row["code"] if row else ""


def set_favorite(result_id, on=True):
    conn = db_conn()
    c = conn.cursor()
    if on:
        c.execute(
            """UPDATE results
               SET is_favorite = 1,
                   human_reviewed_at = ?,
                   expires_at = NULL
               WHERE id = ?""",
            (datetime.now().isoformat(), result_id),
        )
    else:
        c.execute(
            "UPDATE results SET is_favorite = 0 WHERE id = ?",
            (result_id,),
        )
    conn.commit()
    conn.close()


def update_note(result_id, note):
    conn = db_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE results SET human_note = ? WHERE id = ?",
        (note, result_id),
    )
    conn.commit()
    conn.close()


# ── 인증 ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = request.form.get("password", "")
        if pw == DASHBOARD_PASSWORD:
            session.permanent = True
            session["auth"] = True
            return redirect("/")
        return Response(LOGIN_HTML.replace("{{ERR}}", "비밀번호가 틀렸습니다"),
                        mimetype="text/html")
    return Response(LOGIN_HTML.replace("{{ERR}}", ""), mimetype="text/html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── API ──────────────────────────────────────────────
@app.route("/api/status")
@login_required
def api_status():
    return jsonify(get_status())


@app.route("/api/logs")
@login_required
def api_logs():
    return jsonify(get_logs())


# 생산로그(output.log)를 사이클 단위로 잘라 돌려주는 API.
@app.route("/api/runlog")
@login_required
def api_runlog():
    return jsonify(get_log_cycles())


@app.route("/api/favorites")
@login_required
def api_favorites():
    return jsonify(get_favorites())


@app.route("/api/code/<int:rid>")
@login_required
def api_code(rid):
    return jsonify({"code": get_code(rid)})


@app.route("/api/favorite/<int:rid>", methods=["POST"])
@login_required
def api_favorite(rid):
    on = request.json.get("on", True) if request.is_json else True
    set_favorite(rid, on)
    return jsonify({"ok": True})


@app.route("/api/note/<int:rid>", methods=["POST"])
@login_required
def api_note(rid):
    note = request.json.get("note", "") if request.is_json else ""
    update_note(rid, note)
    return jsonify({"ok": True})


@app.route("/")
@login_required
def index():
    return Response(PAGE_HTML, mimetype="text/html")


# ── 로그인 페이지 HTML ────────────────────────────────
LOGIN_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<title>접속 — AI Auto Coder</title>
<style>
  :root { --bg:#0d0f14; --panel:#161a22; --line:#2a2f3a; --amber:#ffb700; --ink:#e8eaed; --dim:#7a828f; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;
         min-height:100vh; display:flex; align-items:center; justify-content:center; padding:24px; }
  .box { width:100%; max-width:360px; background:var(--panel);
         border:1px solid var(--line); border-radius:16px; padding:28px; }
  h1 { font-size:18px; margin:0 0 4px; letter-spacing:1px; }
  .sub { color:var(--dim); font-size:12px; margin-bottom:24px; }
  input { width:100%; padding:14px; background:#0d0f14; border:1px solid var(--line);
          border-radius:10px; color:var(--ink); font-size:16px; font-family:inherit; }
  button { width:100%; margin-top:14px; padding:14px; background:var(--amber); color:#0d0f14;
           border:none; border-radius:10px; font-size:16px; font-weight:700; font-family:inherit; }
  .err { color:#ff5c5c; font-size:12px; margin-top:12px; min-height:14px; }
</style>
</head>
<body>
  <form class="box" method="post">
    <h1>🏭 AI AUTO CODER</h1>
    <div class="sub">접속 인증</div>
    <input type="password" name="password" placeholder="비밀번호" autofocus>
    <button type="submit">입장</button>
    <div class="err">{{ERR}}</div>
  </form>
</body>
</html>"""


# ── 메인 대시보드 HTML ────────────────────────────────
PAGE_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Auto Coder">
<title>AI Auto Coder</title>
<style>
  :root{
    --bg:#0d0f14; --panel:#161a22; --panel2:#1c2129; --line:#2a2f3a;
    --amber:#ffb700; --green:#3ddc84; --red:#ff5c5c; --ink:#e8eaed; --dim:#7a828f;
    --safe-top:env(safe-area-inset-top); --safe-bot:env(safe-area-inset-bottom);
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace;
       padding-bottom:calc(72px + var(--safe-bot));}

  /* 헤더 */
  header{position:sticky;top:0;z-index:10;background:rgba(13,15,20,.92);
         backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
         padding:calc(14px + var(--safe-top)) 18px 14px;
         display:flex;align-items:center;justify-content:space-between;}
  .brand{font-size:15px;letter-spacing:1px;font-weight:700;}
  .pulse{display:inline-block;width:8px;height:8px;border-radius:50%;
         background:var(--dim);margin-right:7px;vertical-align:middle;}
  .pulse.on{background:var(--green);box-shadow:0 0 8px var(--green);
            animation:blink 1.6s infinite;}
  @keyframes blink{50%{opacity:.4;}}
  .logout{color:var(--dim);font-size:11px;text-decoration:none;border:1px solid var(--line);
          padding:6px 10px;border-radius:8px;}

  /* 본문 */
  main{padding:16px;}
  .view{display:none;animation:fade .25s ease;}
  .view.active{display:block;}
  @keyframes fade{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}

  /* 카드 */
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
        padding:16px;margin-bottom:12px;}
  .stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;}
  .stat .label{color:var(--dim);font-size:11px;letter-spacing:.5px;}
  .stat .value{font-size:26px;font-weight:700;margin-top:6px;}
  .stat .value.amber{color:var(--amber);}
  .status-line{display:flex;align-items:center;font-size:14px;margin-bottom:6px;}
  .status-sub{color:var(--dim);font-size:12px;}

  /* 로그 행 */
  .row{display:flex;align-items:center;gap:10px;padding:12px 0;
       border-bottom:1px solid var(--line);}
  .row:last-child{border-bottom:none;}
  .time{color:var(--dim);font-size:12px;min-width:42px;}
  .topic{flex:1;font-size:13px;line-height:1.4;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .score{font-size:12px;font-weight:700;min-width:30px;text-align:right;}
  .score.hi{color:var(--amber);} .score.lo{color:var(--dim);}

  /* 버튼 (터치 44px+) */
  .btn{min-height:38px;padding:8px 12px;border-radius:9px;font-size:13px;
       font-family:inherit;border:1px solid var(--line);background:var(--panel2);
       color:var(--ink);font-weight:600;}
  .btn.star{background:var(--amber);color:#0d0f14;border-color:var(--amber);}
  .btn.ghost{background:transparent;color:var(--dim);}
  .btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;}

  /* 즐겨찾기 카드 */
  .fav-top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
  .fav-note{font-size:14px;font-weight:600;line-height:1.4;}
  .fav-topic{color:var(--dim);font-size:11px;margin-top:3px;}
  .fav-score{color:var(--amber);font-size:13px;font-weight:700;white-space:nowrap;}

  .empty{color:var(--dim);text-align:center;padding:40px 0;font-size:13px;}

  /* 하단 탭 */
  nav{position:fixed;bottom:0;left:0;right:0;z-index:20;
      background:rgba(13,15,20,.96);backdrop-filter:blur(10px);
      border-top:1px solid var(--line);
      display:flex;padding-bottom:var(--safe-bot);}
  nav button{flex:1;background:none;border:none;color:var(--dim);
             padding:12px 0;font-family:inherit;font-size:11px;font-weight:600;}
  nav button .ic{display:block;font-size:20px;margin-bottom:3px;}
  nav button.active{color:var(--amber);}

  /* 모달 (코드 보기) */
  .modal{position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.7);
         display:none;align-items:flex-end;}
  .modal.open{display:flex;}
  .sheet{background:var(--panel);border-top-left-radius:18px;border-top-right-radius:18px;
         width:100%;max-height:85vh;display:flex;flex-direction:column;
         padding:18px;padding-bottom:calc(18px + var(--safe-bot));}
  .sheet h3{margin:0 0 12px;font-size:14px;}
  pre{flex:1;overflow:auto;background:#0a0c10;border:1px solid var(--line);
      border-radius:10px;padding:14px;font-size:12px;line-height:1.5;
      -webkit-overflow-scrolling:touch;}
  .toast{position:fixed;bottom:90px;left:50%;transform:translateX(-50%);
         background:var(--green);color:#0d0f14;padding:10px 18px;border-radius:20px;
         font-size:13px;font-weight:700;opacity:0;transition:opacity .2s;z-index:60;}
  .toast.show{opacity:1;}
</style>
</head>
<body>
  <header>
    <div class="brand"><span id="pulse" class="pulse"></span>AI AUTO CODER</div>
    <a class="logout" href="/logout">로그아웃</a>
  </header>

  <main>
    <!-- 현황 -->
    <section id="view-status" class="view active">
      <div class="card">
        <div class="status-line"><span id="run-dot" class="pulse"></span><span id="run-text">상태 확인 중…</span></div>
        <div class="status-sub">마지막 작업 <b id="last-at">--:--</b> · 오늘 저장 <b id="today">0</b>개</div>
      </div>
      <div class="stat-grid">
        <div class="stat"><div class="label">총 저장</div><div id="s-total" class="value">0</div></div>
        <div class="stat"><div class="label">⭐ 즐겨찾기</div><div id="s-fav" class="value amber">0</div></div>
        <div class="stat"><div class="label">검토 대기</div><div id="s-unrev" class="value">0</div></div>
        <div class="stat"><div class="label">평균 평점</div><div id="s-avg" class="value">0.0</div></div>
      </div>
    </section>

    <!-- 즐겨찾기 -->
    <section id="view-fav" class="view">
      <div id="fav-list"></div>
    </section>

    <!-- 로그 -->
    <section id="view-log" class="view">
      <div class="card" id="log-list"></div>
    </section>

    <!-- 흐름(사이클 전체 로그) -->
    <section id="view-flow" class="view">
      <div id="flow-list"></div>
    </section>
  </main>

  <nav>
    <button class="tab active" data-v="status"><span class="ic">📊</span>현황</button>
    <button class="tab" data-v="fav"><span class="ic">⭐</span>즐겨찾기</button>
    <button class="tab" data-v="log"><span class="ic">🏭</span>생산로그</button>
    <button class="tab" data-v="flow"><span class="ic">🔬</span>흐름</button>
  </nav>

  <!-- 코드 모달 -->
  <div class="modal" id="modal">
    <div class="sheet">
      <h3 id="modal-title">코드</h3>
      <pre id="modal-code"></pre>
      <div class="btn-row">
        <button class="btn star" style="flex:1" onclick="copyCode()">📋 코드 복사</button>
        <button class="btn ghost" onclick="closeModal()">닫기</button>
      </div>
    </div>
  </div>

  <div class="toast" id="toast">복사됨</div>

<script>
const $ = id => document.getElementById(id);
let currentCode = "";

// ── 탭 전환 ──
document.querySelectorAll('.tab').forEach(b=>{
  b.onclick = ()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    const v = b.dataset.v;
    $('view-'+v).classList.add('active');
    if(v==='fav') loadFav();
    if(v==='log') loadLog();
    if(v==='status') loadStatus();
    if(v==='flow') loadFlow();
  };
});

// ── 현황 ──
async function loadStatus(){
  const r = await fetch('/api/status'); const d = await r.json();
  $('run-text').textContent = d.running ? '🟢 라인 가동 중' : '🔴 대기 / 중지됨';
  $('run-dot').className = 'pulse' + (d.running?' on':'');
  $('pulse').className = 'pulse' + (d.running?' on':'');
  $('last-at').textContent = d.last_label;
  $('today').textContent = d.today_saved;
  $('s-total').textContent = d.total;
  $('s-fav').textContent = d.favorites;
  $('s-unrev').textContent = d.unreviewed;
  $('s-avg').textContent = d.avg_score.toFixed(2);
}

// ── 로그 ──
async function loadLog(){
  const r = await fetch('/api/logs'); const rows = await r.json();
  const box = $('log-list');
  if(!rows.length){ box.innerHTML='<div class="empty">아직 생산 기록이 없습니다</div>'; return; }
  box.innerHTML = rows.map(x=>`
    <div class="row">
      <span class="time">${x.time}</span>
      <span class="topic">${esc(x.topic)}</span>
      <span class="score ${x.score>=4?'hi':'lo'}">${x.score.toFixed(1)}</span>
      ${x.score>=4 ? `<button class="btn ${x.is_favorite?'ghost':'star'}"
          onclick="toggleFav(${x.id}, ${!x.is_favorite}, 'log')">${x.is_favorite?'해제':'⭐'}</button>` : ''}
    </div>`).join('');
}

// ── 흐름(사이클 전체 로그) ──
//  /api/runlog 가 잘라준 사이클 목록을 카드로 보여주고,
//  카드를 누르면 코드 모달을 재활용해 그 사이클의 전체 로그(SSH 화면)를 띄웁니다.
let flowCycles = [];   // 마지막으로 받아온 사이클들(모달에서 본문 꺼내쓰기용)

async function loadFlow(){
  const r = await fetch('/api/runlog'); flowCycles = await r.json();
  const box = $('flow-list');
  if(!flowCycles.length){
    box.innerHTML='<div class="empty">로그가 아직 없습니다 (output.log 확인)</div>';
    return;
  }
  box.innerHTML = flowCycles.map(x=>{
    // 점수 뱃지: 폐기면 빨강, 4점 이상이면 금색, 그 외 회색.
    let badge = '';
    if(x.score !== null){
      const cls = x.discarded ? 'lo' : (x.score>=4 ? 'hi' : 'lo');
      const tag = x.discarded ? `${x.score.toFixed(1)} 폐기` : x.score.toFixed(1);
      badge = `<span class="score ${cls}">${tag}</span>`;
    } else {
      badge = `<span class="score lo">진행중</span>`;
    }
    return `
    <div class="card" onclick="viewFlow(${x.idx})" style="cursor:pointer">
      <div class="fav-top">
        <div class="fav-note">${esc(x.title)}</div>
        ${badge}
      </div>
      <div class="fav-topic">탭하여 전체 흐름 보기 →</div>
    </div>`;
  }).join('');
}

// 사이클 하나의 전체 로그를 모달에 띄웁니다(코드 모달 재활용).
function viewFlow(idx){
  const c = flowCycles.find(x=>x.idx===idx);
  if(!c) return;
  currentCode = c.body || '';        // 복사 버튼이 이 값을 그대로 복사함
  $('modal-title').textContent = c.title || '작업 흐름';
  $('modal-code').textContent = currentCode;
  $('modal').classList.add('open');
}

// ── 즐겨찾기 ──
async function loadFav(){
  const r = await fetch('/api/favorites'); const rows = await r.json();
  const box = $('fav-list');
  if(!rows.length){ box.innerHTML='<div class="empty">⭐ 즐겨찾기가 비어 있습니다</div>'; return; }
  box.innerHTML = rows.map(x=>`
    <div class="card">
      <div class="fav-top">
        <div>
          <div class="fav-note" id="note-${x.id}">${esc(x.note)}</div>
          <div class="fav-topic">${esc(x.topic)}</div>
        </div>
        <div class="fav-score">${x.score.toFixed(1)}</div>
      </div>
      <div class="btn-row">
        <button class="btn" onclick="viewCode(${x.id}, '${escAttr(x.note)}')">📄 코드</button>
        <button class="btn" onclick="editNote(${x.id})">✏️ 메모</button>
        <button class="btn ghost" onclick="toggleFav(${x.id}, false, 'fav')">해제</button>
      </div>
    </div>`).join('');
}

// ── 즐겨찾기 토글 ──
async function toggleFav(id, on, from){
  await fetch('/api/favorite/'+id, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({on})});
  toast(on?'⭐ 추가됨':'해제됨');
  if(from==='log') loadLog(); else loadFav();
}

// ── 메모 수정 ──
async function editNote(id){
  const cur = $('note-'+id).textContent;
  const v = prompt('메모 수정', cur);
  if(v===null) return;
  await fetch('/api/note/'+id, {method:'POST',
    headers:{'Content-Type':'application/json'}, body:JSON.stringify({note:v})});
  toast('메모 저장됨'); loadFav();
}

// ── 코드 보기 ──
async function viewCode(id, title){
  const r = await fetch('/api/code/'+id); const d = await r.json();
  currentCode = d.code || '';
  $('modal-title').textContent = title;
  $('modal-code').textContent = currentCode;
  $('modal').classList.add('open');
}
function closeModal(){ $('modal').classList.remove('open'); }

// ── 복사 (http 폴백 포함) ──
async function copyCode(){
  const txt = currentCode;
  try{
    if(navigator.clipboard && window.isSecureContext){
      await navigator.clipboard.writeText(txt);
    } else {
      const ta = document.createElement('textarea');
      ta.value = txt; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      document.execCommand('copy'); document.body.removeChild(ta);
    }
    toast('📋 복사됨');
  }catch(e){ toast('복사 실패 — 길게 눌러 선택'); }
}

// ── 유틸 ──
function esc(s){ return (s||'').replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function escAttr(s){ return (s||'').replace(/'/g,"\\\\'").replace(/"/g,'&quot;'); }
let tt;
function toast(msg){ const t=$('toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(tt); tt=setTimeout(()=>t.classList.remove('show'),1500); }

// 초기 로드 + 30초마다 현황 갱신
loadStatus();
setInterval(()=>{ if($('view-status').classList.contains('active')) loadStatus(); }, 30000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
