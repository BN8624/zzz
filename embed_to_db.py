# =============================================================
#  embed_to_db.py  ―  README 를 임베딩해 research.db 에 저장
# -------------------------------------------------------------
#  하는 일:
#    collect_test.py 가 만든 embedding_requests.jsonl 을 읽어서,
#    무료 AI Studio 키로 한 건씩 임베딩하고(gemini-embedding-2, 3072차원),
#    sqlite-vec 가상테이블이 있는 research.db 에 저장합니다.
#
#  *** 중요: 한 건씩 순차 처리 ***
#    이 임베딩 모델은 리스트를 넣으면 "하나로 합친" 벡터를 주므로,
#    각 문서를 따로따로 호출해야 합니다(개별 임베딩).
#
#  *** 이어하기(resume) ***
#    무료키 RPD(하루 호출 한도)에 걸리면 중간에 멈출 수 있습니다.
#    이미 저장한 key 는 건너뛰므로, 다음 날 그냥 다시 실행하면
#    멈춘 지점부터 이어서 채웁니다. (몇 번을 다시 돌려도 안전)
#
#  실행:  python3 embed_to_db.py
# =============================================================

import os
import json
import time
import struct
import sqlite3

import sqlite_vec
from dotenv import load_dotenv
from google import genai

load_dotenv()

# -------------------------------------------------------------
#  설정
# -------------------------------------------------------------
INPUT_JSONL = "embedding_requests.jsonl"   # collect_test.py 결과(임베딩할 텍스트)
META_JSON = "collected_meta.json"          # repo 메타(제목/별/URL 등)
DB_PATH = "research.db"                     # 만들 자료 DB
EMBED_MODEL = "gemini-embedding-2"
DIM = 3072                                  # 이 모델의 임베딩 차원(확인됨)

# 무료키 사용(자동 코딩 루프와 같은 키). 자료 수집은 일회성이라 분리 실행.
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

# 호출 사이 간격(초). 무료키 분당 한도(RPM)에 안 걸리게 여유를 둡니다.
SLEEP_BETWEEN = 1.0


# -------------------------------------------------------------
#  벡터 ↔ bytes 변환 (sqlite-vec 는 float 들을 이어붙인 bytes 로 저장)
# -------------------------------------------------------------
def serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


# -------------------------------------------------------------
#  DB 준비 ― 두 개의 표를 만듭니다.
#    1) docs   : 사람이 읽는 메타데이터(어떤 repo 인지, 본문 등) — 평범한 표
#    2) vec_docs: 임베딩 벡터 전용 가상테이블(sqlite-vec) — 빠른 유사도검색용
#   둘은 rowid 로 짝지어집니다(같은 번호 = 같은 문서).
# -------------------------------------------------------------
def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)   # 로딩 끝났으면 다시 잠가 안전하게

    conn.execute("""
        CREATE TABLE IF NOT EXISTS docs (
            id        INTEGER PRIMARY KEY,   -- vec_docs 의 rowid 와 같은 값
            key       TEXT UNIQUE,           -- collect 단계의 doc_N (중복 방지/이어하기용)
            full_name TEXT,                  -- owner/repo
            stars     INTEGER,
            description TEXT,
            text      TEXT,                  -- 임베딩한 README 본문
            source_url TEXT
        )
    """)
    # 벡터 가상테이블(3072차원). rowid 로 docs 와 연결.
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_docs USING vec0(
            embedding float[{DIM}]
        )
    """)
    conn.commit()
    return conn


# -------------------------------------------------------------
#  이미 저장된 key 목록 ― 이어하기에서 건너뛰기 위함.
# -------------------------------------------------------------
def already_done(conn):
    rows = conn.execute("SELECT key FROM docs").fetchall()
    return {r[0] for r in rows}


# -------------------------------------------------------------
#  임베딩 한 건 ― 실패 시 잠깐 쉬고 재시도. 끝내 실패하면 None.
# -------------------------------------------------------------
def embed_one(text, max_retries=3):
    for attempt in range(max_retries):
        try:
            r = client.models.embed_content(model=EMBED_MODEL, contents=text)
            return list(r.embeddings[0].values)
        except Exception as e:
            msg = str(e)
            # 하루 한도(RPD) 초과면 더 시도해도 소용없으니 특별 신호로 알림.
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                raise RuntimeError("RPD_LIMIT")  # 위에서 잡아서 깔끔히 중단
            wait = (attempt + 1) * 10
            print(f"    [!] 임베딩 실패(시도 {attempt+1}/{max_retries}): {msg[:60]}")
            print(f"        {wait}초 후 재시도")
            time.sleep(wait)
    return None


def main():
    # 입력/메타 읽기
    with open(INPUT_JSONL, encoding="utf-8") as f:
        reqs = [json.loads(line) for line in f if line.strip()]
    try:
        with open(META_JSON, encoding="utf-8") as f:
            meta = {m["key"]: m for m in json.load(f)}
    except FileNotFoundError:
        meta = {}

    conn = open_db()
    done = already_done(conn)

    total = len(reqs)
    todo = [r for r in reqs if r["key"] not in done]
    print(f"전체 {total}건 / 이미 완료 {len(done)}건 / 이번에 할 일 {len(todo)}건\n")

    if not todo:
        print("✅ 모두 임베딩 완료됨. (research.db 준비됨)")
        conn.close()
        return

    saved = 0
    try:
        for i, req in enumerate(todo, 1):
            key = req["key"]
            text = req["request"]["content"]["parts"][0]["text"]
            m = meta.get(key, {})

            vec = embed_one(text)
            if vec is None:
                print(f"    [skip] {key} — 임베딩 실패, 건너뜀")
                continue

            # docs 에 메타 저장 → 그 rowid 를 vec_docs 에 같은 번호로 저장.
            cur = conn.execute(
                """INSERT INTO docs (key, full_name, stars, description, text, source_url)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (key, m.get("full_name", ""), m.get("stars", 0),
                 m.get("description", ""), text, m.get("source_url", "")),
            )
            rowid = cur.lastrowid
            conn.execute(
                "INSERT INTO vec_docs (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize(vec)),
            )
            conn.commit()
            saved += 1
            print(f"    [{i}/{len(todo)}] {m.get('full_name', key)} 저장됨")
            time.sleep(SLEEP_BETWEEN)

    except RuntimeError as e:
        if str(e) == "RPD_LIMIT":
            print(f"\n⏸  하루 호출 한도(RPD) 도달 — 여기서 중단합니다.")
            print(f"   지금까지 {saved}건 저장. 내일(태평양 자정 이후) 다시 실행하면 이어서 채웁니다.")
            conn.close()
            return
        raise

    print(f"\n✅ 완료! 이번 실행에서 {saved}건 저장.")
    print(f"   research.db 총 문서 수: {conn.execute('SELECT COUNT(*) FROM docs').fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
