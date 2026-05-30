# =============================================================
#  search_db.py  ―  research.db 검색 테스트
# -------------------------------------------------------------
#  하는 일:
#    research.db 에 저장된 자료를, 질문 텍스트와 의미가 가까운 순으로
#    찾아 보여줍니다. (자료 DB 가 잘 만들어졌는지 확인 + 검색 사용법 예시)
#
#  *** sqlite-vec KNN 쿼리 주의 ***
#    MATCH 와 LIMIT 은 반드시 같은 (서브)쿼리 안에 있어야 합니다.
#    JOIN 으로 LIMIT 을 분리하면 "LIMIT or k=? required" 오류가 납니다.
#    그래서 아래처럼 KNN 을 서브쿼리로 먼저 뽑고, 그 결과를 docs 와 조인합니다.
#
#  실행:  python3 search_db.py "비동기 웹 크롤러"
# =============================================================

import os
import sys
import struct
import sqlite3

import sqlite_vec
from dotenv import load_dotenv
from google import genai

load_dotenv()

DB_PATH = "research.db"
EMBED_MODEL = "gemini-embedding-2"
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))


def serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def search(query, top_k=5):
    """질문을 임베딩해서 가장 가까운 자료 top_k 개를 돌려줍니다."""
    # 1) 질문을 같은 모델로 임베딩 (검색용)
    r = client.models.embed_content(model=EMBED_MODEL, contents=query)
    qvec = serialize(list(r.embeddings[0].values))

    conn = open_db()
    # 2) KNN 서브쿼리(MATCH+LIMIT 함께) → docs 와 조인해 메타까지 가져오기
    rows = conn.execute(
        """
        SELECT d.full_name, d.stars, d.description, k.distance
        FROM (
            SELECT rowid, distance
            FROM vec_docs
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
        ) k
        JOIN docs d ON d.id = k.rowid
        ORDER BY k.distance
        """,
        (qvec, top_k),
    ).fetchall()
    conn.close()
    return rows


def main():
    if len(sys.argv) < 2:
        print('사용법: python3 search_db.py "찾을 내용"')
        return
    query = sys.argv[1]
    print(f"🔎 검색: {query}\n")
    for full_name, stars, desc, dist in search(query):
        print(f"  ⭐{stars:>7}  {full_name}")
        print(f"           {(desc or '')[:70]}")
        print(f"           (거리 {dist:.3f})\n")


if __name__ == "__main__":
    main()
