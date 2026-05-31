# =============================================================
#  embed_to_db.py  ―  README 를 "구조화 요약 + 임베딩"해서 research.db 에 저장
#                     (비동기 병렬 버전 — 유료키 한도 활용, 5천건 몇 분)
# -------------------------------------------------------------
#  흐름 (각 README 마다, 병렬로):
#    1. 버텍스 Flash-Lite(유료)로 구조화 요약(JSON):
#         summary / core_idea / mvp_idea / skills / tags
#    2. summary 만 유료 AI Studio 키로 임베딩(embedding-2, 3072차원)
#    3. research.db 에 저장
#
#  *** 키 3종 분리 ***
#    - 요약   : VERTEX_API_KEY      (버텍스 Flash-Lite, RPM 4k)
#    - 임베딩 : PAID_API_KEY        (유료 AI Studio, RPM 3k / RPD 무제한)
#    - (자동코딩 루프의 GOOGLE_API_KEY 무료키는 안 건드림)
#
#  *** 병렬 + 메모리 안전 ***
#    - 동시 실행은 CONCURRENCY 개로 제한(세마포어). 한도/RAM 둘 다 보호.
#    - BATCH 개씩 모아 DB 에 저장하고 비웁니다(메모리 안 쌓이게).
#
#  *** 이어하기 ***
#    이미 저장된 key 는 건너뜀. 중간에 멈춰도 다시 실행하면 이어서.
#
#  실행:  python3 embed_to_db.py
# =============================================================

import os
import re
import json
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

# 동시 실행 개수. RPM 한도(요약4k/임베딩3k)와 e2-micro RAM 둘 다 고려해 보수적으로.
#  8이면 순간 버스트(초당 몰림)를 피해 429를 막습니다. 한도엔 여유가 큽니다.
CONCURRENCY = 8
#  이 개수만큼 모이면 DB 에 저장하고 메모리를 비웁니다.
BATCH = 100

embed_client = genai.Client(api_key=os.getenv("PAID_API_KEY"))                    # 임베딩(유료)
summary_client = genai.Client(vertexai=True, api_key=os.getenv("VERTEX_API_KEY")) # 요약(버텍스)


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


async def process_one(req, meta, sem):
    """README 하나: 요약 → summary 임베딩. 결과 튜플 또는 None(실패)."""
    key = req["key"]
    text = req["request"]["content"]["parts"][0]["text"]
    m = meta.get(key, {})
    name = m.get("full_name", key)

    async with sem:
        await asyncio.sleep(0.3)  # 버스트 분산(초당 몰림 방지)
        # 1) 요약 (버텍스 Flash-Lite)
        try:
            r = await summary_client.aio.models.generate_content(
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
        except Exception as e:
            print(f"      [!] 요약 실패 {name}: {str(e)[:50]}")
            return None

        # 2) summary 임베딩 (유료 AI Studio)
        try:
            er = await embed_client.aio.models.embed_content(
                model=EMBED_MODEL, contents=s["summary"]
            )
            vec = list(er.embeddings[0].values)
        except Exception as e:
            print(f"      [!] 임베딩 실패 {name}: {str(e)[:50]}")
            return None

    return (key, name, m.get("source_url", ""), m.get("stars", 0),
            s.get("summary", ""), s.get("core_idea", ""), s.get("mvp_idea", ""),
            json.dumps(s.get("skills", []), ensure_ascii=False),
            json.dumps(s.get("tags", []), ensure_ascii=False),
            vec)


def save_batch(conn, results):
    """결과 묶음을 DB 에 저장(낱개 행으로). vec_docs 는 rowid 로 docs 와 연결."""
    import time
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for r in results:
        if r is None:
            continue
        key, name, url, stars, summary, core, mvp, skills, tags, vec = r
        cur = conn.execute(
            """INSERT OR IGNORE INTO docs
               (key, full_name, url, stars, summary, core_idea, mvp_idea, skills, tags, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (key, name, url, stars, summary, core, mvp, skills, tags, now),
        )
        if cur.lastrowid and cur.rowcount:
            conn.execute("INSERT INTO vec_docs (rowid, embedding) VALUES (?, ?)",
                         (cur.lastrowid, serialize(vec)))
            n += 1
    conn.commit()
    return n


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
    print(f"동시 {CONCURRENCY}개 병렬, {BATCH}개마다 저장\n")
    if not todo:
        print("✅ 모두 완료됨.")
        conn.close()
        return

    sem = asyncio.Semaphore(CONCURRENCY)
    saved = 0
    # BATCH 단위로 끊어서: 메모리 안 쌓이게 + 중간 저장(이어하기 안전)
    for b in range(0, len(todo), BATCH):
        chunk = todo[b:b + BATCH]
        results = await asyncio.gather(*[process_one(r, meta, sem) for r in chunk])
        n = save_batch(conn, results)
        saved += n
        print(f"    [{min(b+BATCH, len(todo))}/{len(todo)}] 누적 저장 {saved}건")

    print(f"\n✅ 완료! 이번 실행 {saved}건 저장.")
    print(f"   research.db 총 문서: {conn.execute('SELECT COUNT(*) FROM docs').fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
