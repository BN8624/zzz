# =============================================================
#  topic_generator.py  ―  코딩 주제 자동 생성 (+ 임베딩 중복 방지)
# -------------------------------------------------------------
#  하는 일:
#   사용자가 아이디어를 안 내도, 시스템이 스스로
#   "오늘 만들 코딩 주제"를 하나 만들어냅니다.
#
#   ★ 이번 변경(②번): 중복 방지를 "텍스트 20개 회피"에서
#     "임베딩 의미 유사도"로 올렸습니다.
#       1) 26B 가 주제를 만들면 → 그 주제를 무료키로 임베딩하고
#       2) 과거에 통과했던 주제들과 코사인 유사도를 비교해서
#       3) 너무 비슷하면(0.8 초과) 버리고 다시 만듭니다(최대 3회).
#       4) 통과한 주제는 임베딩과 함께 research.db 의
#          topic_history 테이블에 저장해 둡니다(다음 비교용).
#
#   임베딩이란?
#     글의 "의미"를 숫자 목록(벡터)으로 바꾼 것. 단어가 정확히
#     같지 않아도 의미가 비슷하면 숫자도 비슷해집니다. 그래서
#     "RSS 파서"와 "피드 수집기"처럼 표현만 다른 중복도 잡아냅니다.
#
#  사용 모델:
#   - 26B (call_gemma_26b)        : 주제 생성 (지시·디버깅·주제생성 담당)
#   - gemini-embedding-2 (무료키) : 주제 임베딩 (중복 판정용)
#     ※ 루프당 주제 1건만 임베딩이라 무료 분당 60 한도 안에 넉넉합니다.
# =============================================================

import os
import struct
import sqlite3

import numpy as np
import sqlite_vec
from google import genai

from config import GOOGLE_API_KEY, MODELS
from agents import call_gemma_26b
from logger import log_station
from database import CodeDatabase   # 최근 주제 조회(텍스트 회피)용

# 임베딩 전용 클라이언트.
#  agents.py 의 client 와 같은 무료키를 쓰지만, 임베딩은 별도로 부르므로
#  여기서 클라이언트를 하나 더 두는 게 의존이 깔끔합니다.
_embed_client = genai.Client(api_key=GOOGLE_API_KEY)


# -------------------------------------------------------------
#  벡터(숫자 목록)를 sqlite-vec 가 저장하는 이진 형식으로 바꿉니다.
#   (search_db.py / embed_to_db.py 와 똑같은 방식)
# -------------------------------------------------------------
def _serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


class AutoTopicGenerator:
    """난이도를 섞고, 과거 주제 DB 에서 영감을 받아 매번 다른 주제를 만들고,
       임베딩 유사도로 중복을 걸러내는 주제 생성기."""

    # 난이도 목록. (카테고리는 ①번에서 제거함 — research.db 영감으로 대체)
    COMPLEXITIES = ["간단", "중간", "복잡"]

    # ★ 중복 판정 기준: 과거 주제와 코사인 유사도가 이 값을 넘으면 "너무 비슷"
    #   → 버리고 다시 생성. (1.0 = 완전히 같음, 0 = 무관)
    #   0.85 = 살짝 느슨한 편(웬만큼 비슷해도 통과, 거의 같을 때만 거름).
    SIMILARITY_THRESHOLD = 0.85

    # ★ 재생성 최대 횟수. 이만큼 시도해도 계속 비슷하면 마지막 걸 그냥 받습니다.
    #   (무한루프 방지 + 호출 수 낭비 방지)
    MAX_RETRIES = 3

    # 임베딩 모델 / 차원 (research.db 구축 때와 반드시 동일해야 함)
    EMBED_MODEL = MODELS["embedding"]   # "gemini-embedding-2"
    DIM = 3072

    # research.db 경로. search_db.py / embed_to_db.py 와 동일하게 직접 지정.
    #  (이 시스템은 research.db 경로를 config 가 아니라 각 파일에 직접 둡니다)
    RESEARCH_DB = "research.db"

    def __init__(self):
        # 최근 주제 조회용 DB 연결. (텍스트 회피 목록을 만드는 데 사용)
        self.db = CodeDatabase()
        # research.db (영감 + 주제 이력 저장) 연결을 준비합니다.
        self._init_history()

    # =============================================================
    #  research.db 열기 + topic_history 테이블 준비
    # -------------------------------------------------------------
    #  research.db 는 sqlite-vec 확장을 써야 벡터 검색이 됩니다.
    #  (search_db.py 와 동일한 방식으로 확장을 로드합니다)
    # =============================================================
    def _open_research(self):
        conn = sqlite3.connect(self.RESEARCH_DB)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return conn

    def _init_history(self):
        """주제 이력 테이블이 없으면 만듭니다. (이미 있으면 그대로 둠)
           - topic_history : 통과한 주제 텍스트 보관(사람이 읽기용)
           - vec_topics    : 그 주제들의 임베딩 벡터(유사도 검색용)
           두 표는 rowid 로 짝지어집니다(같은 번호 = 같은 주제)."""
        try:
            conn = self._open_research()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS topic_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT,
                    created_at TEXT
                )
            """)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_topics "
                f"USING vec0(embedding float[{self.DIM}])"
            )
            conn.commit()
            conn.close()
        except Exception as e:
            # 이력 테이블 준비 실패는 치명적이지 않습니다(중복검사만 못 할 뿐).
            log_station("주제실", f"[경고] 이력 테이블 준비 실패(중복검사 생략됨): {e}")

    # =============================================================
    #  주제 한 문장을 임베딩(숫자 벡터)으로 바꿉니다.
    #   실패하면(429 등) None 을 돌려주고, 호출한 쪽이 중복검사를 건너뜁니다.
    # =============================================================
    def _embed(self, text):
        try:
            r = _embed_client.models.embed_content(
                model=self.EMBED_MODEL, contents=text
            )
            return list(r.embeddings[0].values)
        except Exception as e:
            log_station("주제실", f"[경고] 임베딩 실패 — 중복검사 건너뜀: {str(e)[:50]}")
            return None

    # =============================================================
    #  방금 만든 주제가 과거 주제와 "너무 비슷한지" 판정합니다.
    #   돌려주는 값: (너무 비슷한가?, 가장 가까운 유사도)
    #
    #   방법:
    #     1) sqlite-vec KNN 으로 "거리상 가장 가까운 후보 몇 개"만 빠르게 추림
    #        (WHERE embedding MATCH ? AND k = ?  ← search_db.py 와 같은 문법)
    #     2) 그 후보들의 실제 벡터를 꺼내 코사인 유사도로 정확히 재계산
    #        (sqlite-vec 거리값은 L2 라서, 0.8 '유사도' 기준엔 코사인이 맞음)
    # =============================================================
    def _is_duplicate(self, query_vec):
        try:
            conn = self._open_research()

            # 이력이 하나도 없으면 비교할 대상이 없음 → 중복 아님.
            n = conn.execute("SELECT COUNT(*) FROM vec_topics").fetchone()[0]
            if n == 0:
                conn.close()
                return False, 0.0

            # 1) KNN 으로 가까운 후보 rowid 를 최대 5개 추립니다.
            k = min(5, n)
            qblob = _serialize(query_vec)
            knn = conn.execute(
                "SELECT rowid, embedding FROM vec_topics "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (qblob, k),
            ).fetchall()
            conn.close()

            # 2) 후보들과 코사인 유사도를 직접 계산해 가장 큰 값을 봅니다.
            q = np.array(query_vec)
            qn = np.linalg.norm(q)
            best = 0.0
            for _rowid, emb_blob in knn:
                # 저장된 이진 벡터를 다시 숫자 배열로 풀어냅니다.
                v = np.array(struct.unpack(f"{self.DIM}f", emb_blob))
                denom = qn * np.linalg.norm(v)
                if denom == 0:
                    continue
                sim = float(np.dot(q, v) / denom)
                if sim > best:
                    best = sim

            return best > self.SIMILARITY_THRESHOLD, best
        except Exception as e:
            # 중복검사 자체가 실패하면, 주제는 살리고 통과시킵니다(본류 보호).
            log_station("주제실", f"[경고] 중복검사 실패 — 통과 처리: {str(e)[:50]}")
            return False, 0.0

    # =============================================================
    #  통과한 주제를 이력에 저장합니다(텍스트 + 임베딩 한 쌍).
    #   topic_history 의 id 와 vec_topics 의 rowid 를 같게 맞춥니다.
    # =============================================================
    def _save_history(self, topic, vec):
        try:
            from datetime import datetime
            conn = self._open_research()
            cur = conn.execute(
                "INSERT INTO topic_history (topic, created_at) VALUES (?, ?)",
                (topic, datetime.now().isoformat()),
            )
            new_id = cur.lastrowid
            # 같은 id 를 rowid 로 박아 두 표를 짝지웁니다.
            conn.execute(
                "INSERT INTO vec_topics (rowid, embedding) VALUES (?, ?)",
                (new_id, _serialize(vec)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            # 저장 실패해도 이번 주제 진행에는 지장 없습니다(다음 비교에만 영향).
            log_station("주제실", f"[경고] 주제 이력 저장 실패(무시): {str(e)[:50]}")

    # =============================================================
    #  research.db 에서 "영감 재료"를 뽑아옵니다. (①번에서 만든 연결)
    #   랜덤 2개 + 스타 상위 100 중 1개 = 총 3개를 core_idea/mvp_idea 로.
    # =============================================================
    def _fetch_inspiration(self):
        try:
            conn = self._open_research()
            rows = conn.execute(
                "SELECT full_name, core_idea, mvp_idea FROM docs "
                "ORDER BY RANDOM() LIMIT 2"
            ).fetchall()
            top = conn.execute(
                "SELECT full_name, core_idea, mvp_idea FROM docs "
                "ORDER BY stars DESC LIMIT 100"
            ).fetchall()
            conn.close()
            import random as _r
            if top:
                rows = list(rows) + [_r.choice(top)]
            return rows
        except Exception as e:
            log_station("주제실", f"[경고] 영감 조회 실패(무시): {str(e)[:50]}")
            return []

    # =============================================================
    #  주제 한 개를 26B 로 생성합니다(영감 + 회피목록 포함).
    # =============================================================
    def _generate_once(self, complexity):
        # 텍스트 회피 목록(기존 방식 유지 — 1차 거름망 역할).
        recent_topics = self.db.get_recent_topics(20)
        avoid_block = ""
        if recent_topics:
            avoid_list = "\n".join(f"- {t}" for t in recent_topics)
            avoid_block = (
                "\n# 이미 만든 주제 (아래와 겹치거나 비슷한 것 금지)\n"
                f"{avoid_list}\n"
            )

        # research.db 에서 영감 재료를 받아 프롬프트에 넣습니다.
        inspirations = self._fetch_inspiration()
        insp_block = ""
        if inspirations:
            lines = []
            for full_name, core_idea, mvp_idea in inspirations:
                lines.append(f"- {full_name}: {core_idea} / 입문판: {mvp_idea}")
            insp_block = (
                "\n# 참고할 만한 실제 오픈소스 (영감용, 그대로 베끼지 말 것)\n"
                + "\n".join(lines) + "\n"
            )
            # 어떤 깃허브 repo 를 영감으로 넣었는지 로그에 한 줄로 남깁니다.
            #  (repo 이름만. 핵심아이디어까지 찍으면 화면이 너무 길어짐)
            names = ", ".join(full_name for full_name, _c, _m in inspirations)
            log_station("주제실", f"영감 참고: {names}")

        prompt = f"""다음 조건에 맞는 Python 코딩 작업 1개를 제안하세요.

난이도: {complexity}

조건:
- 실용적일 것
- 30분 안에 구현 가능할 것
- 독립적으로 실행 가능할 것
- 아래 '이미 만든 주제'와 겹치거나 비슷하지 않을 것
{avoid_block}{insp_block}
형식: 한 줄로, 구체적인 작업 설명만 쓰세요.
예시: "RSS 피드를 파싱해서 키워드 빈도를 분석하는 스크립트"
"""
        topic = call_gemma_26b(prompt).strip()
        # 여러 줄로 올 수 있으니 첫 줄만 사용합니다.
        return topic.split("\n")[0].strip()

    # =============================================================
    #  바깥에서 부르는 함수 ― 주제 하나를 만들어 돌려줍니다.
    #   (반환 형식은 그대로 {"topic","category","complexity"} 유지 →
    #    main.py 는 topic_info["topic"] 만 쓰므로 안전)
    # =============================================================
    def generate(self):
        import random
        complexity = random.choice(self.COMPLEXITIES)

        log_station("주제실", f"{complexity} 주제 생성 중...")

        topic = None
        vec = None

        # 최대 MAX_RETRIES 번까지: 생성 → 임베딩 → 중복검사 → 통과면 끝.
        for attempt in range(1, self.MAX_RETRIES + 1):
            topic = self._generate_once(complexity)

            # 주제를 임베딩. 실패하면(None) 중복검사를 건너뛰고 그냥 채택.
            vec = self._embed(topic)
            if vec is None:
                log_station("주제실", f"주제 결정(중복검사 생략): {topic}")
                return {"topic": topic, "category": "", "complexity": complexity}

            # 과거 주제와 비교.
            is_dup, sim = self._is_duplicate(vec)
            if not is_dup:
                log_station("주제실", f"주제 결정(유사도 {sim:.2f}): {topic}")
                # 통과한 주제를 이력에 저장(다음 비교용) 후 반환.
                self._save_history(topic, vec)
                return {"topic": topic, "category": "", "complexity": complexity}

            # 너무 비슷하면 다시 생성.
            log_station(
                "주제실",
                f"유사도 {sim:.2f} > {self.SIMILARITY_THRESHOLD} → 재생성 "
                f"({attempt}/{self.MAX_RETRIES})",
            )

        # 여기까지 왔다 = 계속 비슷했음. 마지막 주제를 그냥 받아들이되,
        #  그래도 이력엔 남겨 둡니다(다음부터 이것도 회피 대상이 됨).
        log_station("주제실", f"재생성 한도 도달 — 마지막 주제 채택: {topic}")
        if vec is not None:
            self._save_history(topic, vec)
        return {"topic": topic, "category": "", "complexity": complexity}
