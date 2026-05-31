# =============================================================
#  search_db.py  ―  research.db 검색 테스트
# -------------------------------------------------------------
#  질문 텍스트와 의미가 가까운 자료를 찾아 보여줍니다.
#  (자료 DB 확인 + 검색 사용법 예시)
#
#  *** sqlite-vec KNN: 'k = ?' 문법 필수 ***
#    이 버전은 ORDER BY+LIMIT 대신 'WHERE embedding MATCH ? AND k = ?' 를 요구.
#    KNN 으로 rowid 를 먼저 뽑고, 그 rowid 로 docs 메타를 가져옵니다.
#
#  실행:  python3 search_db.py "비동기 웹 크롤러"
# =============================================================

import os
import sys
import json
import struct
import sqlite3

import sqlite_vec
from dotenv import load_dotenv
from google import genai

load_dotenv()

DB_PATH = "research.db"
EMBED_MODEL = "gemini-embedding-2"
client = genai.Client(api_key=os.getenv("PAID_API_KEY"))   # 검색질문 임베딩(유료키, 한도 높음)


def serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def search(query, top_k=5):
    r = client.models.embed_content(model=EMBED_MODEL, contents=query)
    qvec = serialize(list(r.embeddings[0].values))

    conn = open_db()
    knn = conn.execute(
        "SELECT rowid, distance FROM vec_docs WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (qvec, top_k),
    ).fetchall()

    rows = []
    for rowid, dist in knn:
        d = conn.execute(
            "SELECT full_name, stars, summary, core_idea, mvp_idea, tags FROM docs WHERE id = ?",
            (rowid,),
        ).fetchone()
        if d:
            rows.append((d, dist))
    conn.close()
    return rows


def main():
    if len(sys.argv) < 2:
        print('사용법: python3 search_db.py "찾을 내용"')
        return
    query = sys.argv[1]
    print(f"🔎 검색: {query}\n")
    for (full_name, stars, summary, core_idea, mvp_idea, tags), dist in search(query):
        try:
            tag_list = ", ".join(json.loads(tags or "[]"))
        except Exception:
            tag_list = ""
        print(f"  ⭐{stars:>7}  {full_name}   (거리 {dist:.3f})")
        print(f"     핵심: {core_idea}")
        print(f"     요약: {(summary or '')[:80]}")
        print(f"     MVP : {(mvp_idea or '')[:80]}")
        print(f"     태그: {tag_list}\n")


if __name__ == "__main__":
    main()
