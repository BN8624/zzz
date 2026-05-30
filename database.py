# =============================================================
#  database.py  ―  결과 저장 DB (SQLite)
# -------------------------------------------------------------
#  하는 일:
#   - 완성된 코드 결과를 데이터베이스 파일에 저장합니다.
#   - 단, AI 평점 4점 이상만 저장합니다(그 미만은 폐기).
#   - 점수에 따라 보관 기간(자동 삭제일)을 다르게 둡니다.
#   - 즐겨찾기(⭐) 표시한 것은 영구 보관됩니다.
#   - 만료된 항목을 지우는 자동정리 기능도 여기 있습니다.
#
#  중요:
#   여기서 만드는 표(컬럼) 구조는 dashboard.py 가 읽는 것과
#   똑같아야 합니다. 그래야 대시보드에 결과가 제대로 보입니다.
#
#  SQLite 란?
#   파일 하나(code_results.db)가 곧 데이터베이스인, 가장 간단한 DB.
#   별도 서버 설치가 필요 없어 1인용에 딱 맞습니다.
# =============================================================

import sqlite3
from datetime import datetime, timedelta

from config import DB_PATH, DATA_RETENTION


class CodeDatabase:
    """결과를 저장하고 꺼내는 데이터베이스 관리자."""

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.init_db()   # 시작할 때 표가 없으면 만듭니다.

    # -------------------------------------------------------------
    #  표(table) 만들기 ― 이미 있으면 그대로 둡니다.
    # -------------------------------------------------------------
    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        # WAL 모드: 읽기/쓰기가 동시에 일어나도 덜 충돌하게 해 줍니다.
        # (자동 코딩 루프가 쓰는 동안 대시보드가 읽을 수 있어야 하므로)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,              -- 주제
                category TEXT,                    -- 분류(선택)
                code TEXT,                        -- 생성된 코드
                plan TEXT,                        -- 계획
                review TEXT,                      -- 검토 결과
                ai_score REAL,                    -- AI 평점(0~5)
                ai_summary TEXT,                  -- AI 한줄평
                expires_at TEXT,                  -- 자동 삭제 예정일(없으면 영구)
                is_favorite INTEGER DEFAULT 0,    -- 즐겨찾기 여부(0/1)
                human_note TEXT,                  -- 사용자 메모
                human_reviewed_at TEXT,           -- 사용자가 검토한 시각
                iterations INTEGER,               -- 반복 횟수
                created_at TEXT                   -- 생성 시각
            )
        """)

        # 자주 찾는 열에 색인을 만들어 검색을 빠르게 합니다.
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_score ON results(ai_score)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_expires ON results(expires_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_favorite ON results(is_favorite)")

        conn.commit()
        conn.close()

    # -------------------------------------------------------------
    #  결과 저장 ― 4점 이상만!
    #   점수에 따라 만료일을 계산해서 같이 저장합니다.
    # -------------------------------------------------------------
    def save(self, topic, result):
        evaluation = result.get("evaluation", {})
        score = evaluation.get("total", 0)

        # 기준 점수 미만이면 저장하지 않고 None 을 돌려줍니다(= 폐기).
        if score < DATA_RETENTION["min_score_to_save"]:
            return None

        # 점수에 맞는 보관 일수를 찾습니다.
        #  예) 4.0→7일, 4.5→14일, 5.0→30일
        #  점수가 높을수록 더 오래 보관하도록 가장 큰 기준을 적용.
        expiry_days = None
        for threshold in sorted(DATA_RETENTION["auto_delete_days"].keys()):
            if score >= threshold:
                expiry_days = DATA_RETENTION["auto_delete_days"][threshold]

        # 만료 예정일(오늘 + 보관일수)을 계산.
        expires_at = None
        if expiry_days:
            expires_at = (datetime.now() + timedelta(days=expiry_days)).isoformat()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO results
            (topic, code, plan, review, ai_score, ai_summary,
             expires_at, iterations, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            topic,
            result.get("code", ""),
            result.get("plan", ""),
            result.get("review", ""),
            score,
            evaluation.get("summary", ""),
            expires_at,
            result.get("iterations", 0),
            datetime.now().isoformat(),
        ))
        result_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return result_id

    # -------------------------------------------------------------
    #  통계 ― 대시보드 현황 탭에서 사용.
    # -------------------------------------------------------------
    def get_stats(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        stats = {}

        cursor.execute("SELECT COUNT(*) FROM results")
        stats["total"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM results WHERE is_favorite = 1")
        stats["favorites"] = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM results WHERE human_reviewed_at IS NULL")
        stats["unreviewed"] = cursor.fetchone()[0]

        cursor.execute("SELECT AVG(ai_score) FROM results")
        stats["avg_score"] = cursor.fetchone()[0] or 0

        conn.close()
        return stats

    # -------------------------------------------------------------
    #  최근 주제 목록 ― 주제 자동생성 시 "중복 회피"에 씁니다.
    #   가장 최근 저장된 주제 N개의 제목만 뽑아 돌려줍니다.
    #   topic_generator 가 이 목록을 보고 "겹치지 않는" 주제를 만듭니다.
    #   (DB에 저장된 건 4점 이상이라, 곧 "쉽게 4점 넘는 주제들" 목록임.
    #    그걸 회피 목록으로 주면 비슷한 주제가 덜 생깁니다.)
    # -------------------------------------------------------------
    def get_recent_topics(self, limit=20):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT topic FROM results ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [r[0] for r in rows]

    # =============================================================
    #  자동정리 ― 만료일이 지난 항목을 삭제합니다.
    #   단, 즐겨찾기(⭐)는 절대 지우지 않습니다.
    #   main.py 가 하루 한 번 이 함수를 부릅니다.
    # =============================================================
    def daily_cleanup(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM results
            WHERE expires_at IS NOT NULL
              AND expires_at < ?
              AND is_favorite = 0
        """, (datetime.now().isoformat(),))
        deleted = cursor.rowcount
        conn.commit()
        conn.execute("VACUUM")   # 삭제 후 빈 공간을 정리해 파일 크기를 줄입니다.
        conn.close()
        print(f"🗑️  자동정리: {deleted}개 삭제됨")
        return deleted
