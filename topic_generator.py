# =============================================================
#  topic_generator.py  ―  코딩 주제 자동 생성 (README 영감판)
# -------------------------------------------------------------
#  하는 일:
#   사용자가 아이디어를 안 내도, 시스템이 스스로
#   "오늘 만들 코딩 주제"를 하나 만들어냅니다.
#
#   *** 바뀐 점 (이전: 카테고리+난이도 랜덤) ***
#   - 미리 정해둔 '카테고리'를 없앴습니다.
#     (카테고리를 박으면 주제가 그 틀 안에 갇혀 매번 비슷해졌음)
#   - 대신 research.db(모아둔 GitHub README 자료)에서 실제
#     프로젝트 3개를 뽑아 "영감 재료"로 26B 에게 보여줍니다.
#     → 매번 다른 분야의 프로젝트가 섞여 들어오니 주제가 다양해집니다.
#   - 3개 구성: 랜덤 2개 + 스타 상위 100개 중 랜덤 1개
#       · 랜덤 2개 = 다양성 (엉뚱하고 신선한 영감)
#       · 스타 상위 1개 = 품질 앵커 (검증된 프로젝트가 중심을 잡아줌)
#       · 상위 "100개 중 랜덤"인 이유: 매번 1등만 뽑으면 그 프로젝트가
#         늘 닻으로 박혀 또 다른 수렴이 생기므로, 상위권 안에서 섞습니다.
#   - 난이도는 그대로 랜덤 유지 (영감은 README 가 주고, 난이도만 조절).
#
#  사용 모델: 26B (call_gemma_26b)
#   → 26B 의 역할(지시·디버깅·주제생성)을 채워 31B 와 사용량 균형.
#
#  중요: research.db 는 '읽기만' 합니다(SELECT). 임베딩 벡터는 쓰지 않고
#        core_idea / mvp_idea 텍스트만 읽으므로, 추가 API 호출이 없습니다.
# =============================================================

import random
import sqlite3

from agents import call_gemma_26b
from logger import log_station
from database import CodeDatabase   # 최근 주제 조회(중복 회피)용


# research.db 경로. embed_to_db.py / search_db.py 와 같은 파일을 가리킵니다.
#  (config 의 DB_PATH 는 결과 DB(code_results.db)라 이름이 겹쳐서, 여기 따로 둡니다.)
RESEARCH_DB_PATH = "research.db"

# "스타 상위 N개" 중에서 앵커 1개를 뽑을 때의 N. (상위 100개 풀에서 랜덤)
TOP_STAR_POOL = 100


class AutoTopicGenerator:
    """README 자료에서 영감을 얻어 매번 다른 주제를 만들어내는 생성기."""

    # 난이도 목록. (카테고리는 없앴고, 난이도만 남김)
    COMPLEXITIES = ["간단", "중간", "복잡"]

    def __init__(self):
        # 최근 주제 조회용 DB 연결. (중복 회피 목록을 만드는 데 사용)
        self.db = CodeDatabase()

    # -------------------------------------------------------------
    #  영감 재료 뽑기 ― research.db 에서 프로젝트 3개를 골라옵니다.
    #   구성: 랜덤 2개 + 스타 상위 100개 중 랜덤 1개.
    #   돌려주는 값: [{full_name, core_idea, mvp_idea}, ...] (최대 3개)
    #
    #   실패해도 절대 예외를 위로 던지지 않습니다.
    #   (자동 루프가 24시간 도는데, 여기서 죽으면 전체가 멈추므로)
    #   못 가져오면 빈 리스트를 돌려주고, 호출한 쪽은 영감 없이 진행합니다.
    # -------------------------------------------------------------
    def _pick_inspiration(self):
        try:
            conn = sqlite3.connect(RESEARCH_DB_PATH)
            cur = conn.cursor()

            picks = []          # 최종으로 고른 행들
            seen_names = set()   # 같은 repo 가 두 번 들어가지 않게

            # (1) 앵커 1개: 스타 상위 100개를 먼저 뽑고, 그 안에서 랜덤 1개.
            #     core_idea 가 비어있는 행은 영감 재료로 못 쓰니 제외합니다.
            top_rows = cur.execute(
                """SELECT full_name, core_idea, mvp_idea
                   FROM docs
                   WHERE core_idea IS NOT NULL AND core_idea != ''
                   ORDER BY stars DESC
                   LIMIT ?""",
                (TOP_STAR_POOL,),
            ).fetchall()
            if top_rows:
                anchor = random.choice(top_rows)
                picks.append(anchor)
                seen_names.add(anchor[0])

            # (2) 랜덤 2개: 전체에서 무작위로. (앵커와 겹치면 건너뜀)
            #     ORDER BY RANDOM() 은 행이 수천 개 수준이면 충분히 빠릅니다.
            rand_rows = cur.execute(
                """SELECT full_name, core_idea, mvp_idea
                   FROM docs
                   WHERE core_idea IS NOT NULL AND core_idea != ''
                   ORDER BY RANDOM()
                   LIMIT 10""",
            ).fetchall()
            for row in rand_rows:
                if len(picks) >= 3:
                    break
                if row[0] in seen_names:
                    continue
                picks.append(row)
                seen_names.add(row[0])

            conn.close()

            # 딕셔너리 형태로 정리해서 돌려줍니다.
            return [
                {"full_name": r[0], "core_idea": r[1], "mvp_idea": r[2]}
                for r in picks
            ]
        except Exception as e:
            # research.db 가 없거나 비어있어도 주제 생성은 계속돼야 합니다.
            log_station("주제실", f"영감 재료 조회 실패(무시하고 진행): {e}")
            return []

    # -------------------------------------------------------------
    #  영감 재료를 프롬프트용 텍스트로 변환.
    #   각 프로젝트의 core_idea(핵심 아이디어)와 mvp_idea(최소 버전)만 넣습니다.
    #   → '설명(summary)'보다 '아이디어'가 주제 생성에 직접 쓸모 있기 때문.
    # -------------------------------------------------------------
    def _format_inspiration(self, inspirations):
        lines = []
        for i, p in enumerate(inspirations, 1):
            lines.append(f"## 참고 프로젝트 {i}: {p['full_name']}")
            lines.append(f"- 핵심 아이디어: {p['core_idea']}")
            if p.get("mvp_idea"):
                lines.append(f"- 최소 버전(MVP): {p['mvp_idea']}")
            lines.append("")   # 프로젝트 사이 빈 줄
        return "\n".join(lines)

    def generate(self):
        """주제 하나를 만들어 돌려줍니다."""
        # 난이도만 무작위로 뽑습니다. (카테고리는 없앰)
        complexity = random.choice(self.COMPLEXITIES)

        log_station("주제실", f"난이도 [{complexity}] / README 영감 기반 주제 생성 중...")

        # (A) research.db 에서 영감 재료 3개 뽑기 (랜덤 2 + 스타 상위 100중 1)
        inspirations = self._pick_inspiration()
        if inspirations:
            names = ", ".join(p["full_name"] for p in inspirations)
            log_station("주제실", f"영감: {names}")
            inspiration_block = (
                "# 영감 재료 (실제 오픈소스 프로젝트들)\n"
                "아래 프로젝트들의 아이디어에서 영감만 얻으세요. 그대로 복제하지 말고,\n"
                "여기서 힌트를 얻어 '새로운' 작업을 만들어내세요. 여러 개를 조합해도 좋습니다.\n\n"
                f"{self._format_inspiration(inspirations)}"
            )
        else:
            # 영감을 못 가져온 경우(research.db 없음 등): 영감 없이 진행.
            inspiration_block = ""

        # (B) 이미 만든 최근 주제들을 "겹치지 마" 목록으로 씁니다.
        #     비슷한 주제가 반복 생성되는 것을 '생성 단계'에서 막습니다.
        recent_topics = self.db.get_recent_topics(20)
        avoid_block = ""
        if recent_topics:
            avoid_list = "\n".join(f"- {t}" for t in recent_topics)
            avoid_block = (
                "\n# 이미 만든 주제 (아래와 겹치거나 비슷한 것 금지)\n"
                f"{avoid_list}\n"
            )

        # (C) 프롬프트 조립.
        prompt = f"""다음 조건에 맞는 Python 코딩 작업 1개를 제안하세요.

난이도: {complexity}

{inspiration_block}
조건:
- 실용적일 것
- 30분 안에 구현 가능할 것
- 독립적으로 실행 가능할 것
- 위 영감 재료를 그대로 베끼지 말고, 힌트만 얻어 새롭게 만들 것
- 아래 '이미 만든 주제'와 겹치거나 비슷하지 않을 것
{avoid_block}
형식: 한 줄로, 구체적인 작업 설명만 쓰세요.
예시: "RSS 피드를 파싱해서 키워드 빈도를 분석하는 스크립트"
"""
        topic = call_gemma_26b(prompt).strip()

        # 여러 줄로 올 수 있으니 첫 줄만 사용합니다.
        topic = topic.split("\n")[0].strip()

        log_station("주제실", f"주제 결정: {topic}")

        return {
            "topic": topic,
            "complexity": complexity,
            # 어떤 프로젝트에서 영감을 얻었는지도 같이 돌려줍니다(로그/추적용).
            "inspirations": [p["full_name"] for p in inspirations],
        }
