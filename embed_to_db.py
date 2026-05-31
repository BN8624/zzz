# =============================================================
#  embed_to_db.py  ―  README 를 "구조화 요약 + 임베딩"해서 research.db 에 저장
# -------------------------------------------------------------
#  속도 전략 (둘의 한도가 다름):
#    - 요약(Flash-Lite, RPM 3k)  → 병렬로 빠르게
#    - 임베딩(embedding-2, 분당 ~60) → 1초 간격 순차 (이게 진짜 병목)
#  그래서 한 배치에서: 요약을 먼저 병렬로 다 받고 → 임베딩을 1초씩 순차로.
#
#  키: 둘 다 PAID_API_KEY (유료 AI Studio, generativelanguage 경로)
#      ※ vertexai=True 는 절대 쓰지 않음 — 같은 프로세스 임베딩까지
#        Vertex 경로로 전염돼 한도가 폭락하는 문제가 있었음.
#
#  이어하기: 이미 저장된 key 는 건너뜀. 중간에 멈춰도 다시 실행하면 이어서.
#
#  실행:  python3 embed_to_db.py   (전체는 nohup 권장)
# =============================================================

import os
import re
import json
import time
import struct
import asyncio
import sqlite3

import sqlite_vec
from dotenv import load_dotenv
from google import genai

load_dotenv()

INPUT_JSONL = "embedding_requests.jsonl"
META_JSON = "collected_meta.json"
DB_PATH = "research.db"

EMBED_MODEL = "gemini-embedding-2"
DIM = 3072
SUMMARY_MODEL = "gemini-3.1-flash-lite"

SUMMARY_CONCURRENCY = 10    # 요약은 병렬(RPM 넉넉)
EMBED_INTERVAL = 1.1        # 임베딩은 1.1초 간격(분당 ~55, 한도 60 안쪽 안전)
BATCH = 50                  # 이만큼 모아서 요약→임베딩→저장 한 묶음

client = genai.Client(api_key=os.getenv("PAID_API_KEY"))   # 요약·임베딩 공용(일반 경로)


def serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS docs (
            id INTEGER PRIMARY KEY, key TEXT UNIQUE, full_name TEXT, url TEXT,
            stars INTEGER, summary TEXT, core_idea TEXT, mvp_idea TEXT,
            skills TEXT, tags TEXT, created_at TEXT
        )
    """)
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_docs USING vec0(embedding float[{DIM}])")
    conn.commit()
    return conn


SUMMARY_PROMPT = """당신은 오픈소스 프로젝트를 초보 개발자에게 설명하는 분석가입니다.

# 프로젝트
{name}

# README (앞부분)
{readme}

# 할 일
이 프로젝트를 분석해 아래 JSON 으로만 답하세요. 설치법/배지/목차는 무시하고
"이게 무엇을, 왜, 어떻게" 하는지에 집중하세요. 모두 한국어로.

- summary: 이 프로젝트가 무엇을 하는 도구인지 3~4문장으로. (가장 중요)
- core_idea: 핵심 아이디어를 한 문장으로.
- mvp_idea: 초보자가 '하루 안에' 만들 수 있는 최소 버전. 무엇부터 만들지 구체적으로.
- skills: 따라 만들며 배우는 핵심 기술 3~6개. (문자열 배열)
- tags: 분류 태그 3~6개. 영어 소문자, 하이픈. (문자열 배열)

# 출력 (이 JSON 객체 하나만, 다른 텍스트 없이)
{{"summary":"...","core_idea":"...","mvp_idea":"...","skills":["..."],"tags":["..."]}}
"""


async def summarize_one(req, meta, sem):
    """요약만 (병렬). 결과 dict 또는 None."""
    key = req["key"]
    text = req["request"]["content"]["parts"][0]["text"]
    m = meta.get(key, {})
    name = m.get("full_name", key)
    async with sem:
        try:
            r = await client.aio.models.generate_content(
                model=SUMMARY_MODEL,
                contents=SUMMARY_PROMPT.format(name=name, readme=text),
            )
            t = (r.text or "").strip()
            mt = re.search(r"\{.*\}", t, re.DOTALL)
            if not mt:
                return None
            s = json.loads(mt.group())
            if not s.get("summary"):
                return None
            return {"key": key, "name": name, "meta": m, "s": s}
        except Exception as e:
            print(f"      [!] 요약 실패 {name}: {str(e)[:45]}")
            return None


def embed_one(text, max_retries=4):
    """임베딩 1건 (동기, 순차). 429 면 점점 더 쉬며 재시도."""
    for attempt in range(max_retries):
        try:
            r = client.models.embed_content(model=EMBED_MODEL, contents=text)
            return list(r.embeddings[0].values)
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                wait = (attempt + 1) * 10   # 10,20,30초
                print(f"      [..] 임베딩 429 — {wait}초 쉬고 재시도")
                time.sleep(wait)
            else:
                print(f"      [!] 임베딩 실패: {str(e)[:45]}")
                return None
    return None


async def main():
    with open(INPUT_JSONL, encoding="utf-8") as f:
        reqs = [json.loads(line) for line in f if line.strip()]
    try:
        with open(META_JSON, encoding="utf-8") as f:
            meta = {m["key"]: m for m in json.load(f)}
    except FileNotFoundError:
        meta = {}

    conn = open_db()
    done = {r[0] for r in conn.execute("SELECT key FROM docs").fetchall()}
    todo = [r for r in reqs if r["key"] not in done]
    print(f"전체 {len(reqs)}건 / 완료 {len(done)}건 / 이번에 할 일 {len(todo)}건")
    print(f"요약 병렬 {SUMMARY_CONCURRENCY} / 임베딩 {EMBED_INTERVAL}초 간격\n")
    if not todo:
        print("✅ 모두 완료됨.")
        conn.close()
        return

    sem = asyncio.Semaphore(SUMMARY_CONCURRENCY)
    saved = 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    for b in range(0, len(todo), BATCH):
        chunk = todo[b:b + BATCH]

        # 1) 요약: 병렬로 한꺼번에
        summaries = await asyncio.gather(*[summarize_one(r, meta, sem) for r in chunk])
        summaries = [x for x in summaries if x]

        # 2) 임베딩: 1.1초 간격 순차 → 저장
        for item in summaries:
            vec = embed_one(item["s"]["summary"])
            if vec is None:
                continue
            s, m, name = item["s"], item["meta"], item["name"]
            cur = conn.execute(
                """INSERT OR IGNORE INTO docs
                   (key, full_name, url, stars, summary, core_idea, mvp_idea, skills, tags, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (item["key"], name, m.get("source_url", ""), m.get("stars", 0),
                 s.get("summary", ""), s.get("core_idea", ""), s.get("mvp_idea", ""),
                 json.dumps(s.get("skills", []), ensure_ascii=False),
                 json.dumps(s.get("tags", []), ensure_ascii=False), now),
            )
            if cur.lastrowid and cur.rowcount:
                conn.execute("INSERT INTO vec_docs (rowid, embedding) VALUES (?, ?)",
                             (cur.lastrowid, serialize(vec)))
                saved += 1
            conn.commit()
            time.sleep(EMBED_INTERVAL)

        print(f"    [{min(b+BATCH, len(todo))}/{len(todo)}] 누적 저장 {saved}건")

    print(f"\n✅ 완료! 이번 실행 {saved}건 저장.")
    print(f"   research.db 총 문서: {conn.execute('SELECT COUNT(*) FROM docs').fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
