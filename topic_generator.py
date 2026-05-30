# =============================================================
#  topic_generator.py  ―  코딩 주제 자동 생성
# -------------------------------------------------------------
#  하는 일:
#   사용자가 아이디어를 안 내도, 시스템이 스스로
#   "오늘 만들 코딩 주제"를 하나 만들어냅니다.
#
#   방법:
#   - 미리 정해둔 카테고리와 난이도 중 무작위로 하나씩 고르고
#   - 그 조건에 맞는 구체적 작업을 26B 모델에게 제안하게 합니다.
#
#  사용 모델: 26B (call_gemma_26b)
#   → 26B 의 역할(지시·디버깅·주제생성)을 채워
#     31B 와 사용량 균형을 맞춥니다.
# =============================================================

import random

from agents import call_gemma_26b
from logger import log_station
from database import CodeDatabase   # 최근 주제 조회(중복 회피)용


class AutoTopicGenerator:
    """카테고리 + 난이도를 섞어 매번 다른 주제를 만들어내는 생성기."""

    # 주제가 나올 수 있는 분야 목록.
    CATEGORIES = [
        "웹 백엔드", "데이터 처리", "알고리즘", "유틸리티",
        "API 통합", "자동화 스크립트", "파일 처리",
        "테스트 코드", "디자인 패턴", "보안/암호화",
    ]

    # 난이도 목록.
    COMPLEXITIES = ["간단", "중간", "복잡"]

    def __init__(self):
        # 최근 주제 조회용 DB 연결. (중복 회피 목록을 만드는 데 사용)
        self.db = CodeDatabase()

    def generate(self):
        """주제 하나를 만들어 돌려줍니다."""
        # 분야와 난이도를 무작위로 하나씩 뽑습니다.
        category = random.choice(self.CATEGORIES)
        complexity = random.choice(self.COMPLEXITIES)

        log_station("주제실", f"{category} / {complexity} 주제 생성 중...")

        # 이미 만든 최근 주제들을 가져와 "겹치지 마" 목록으로 씁니다.
        # 비슷한 주제가 반복 생성되는 것을 '생성 단계'에서 막습니다.
        # (헛코딩을 아예 안 하게 되어, 임베딩 없이도 충분히 효과적입니다.)
        recent_topics = self.db.get_recent_topics(20)
        avoid_block = ""
        if recent_topics:
            avoid_list = "\n".join(f"- {t}" for t in recent_topics)
            avoid_block = (
                "\n# 이미 만든 주제 (아래와 겹치거나 비슷한 것 금지)\n"
                f"{avoid_list}\n"
            )

        prompt = f"""다음 조건에 맞는 Python 코딩 작업 1개를 제안하세요.

카테고리: {category}
난이도: {complexity}

조건:
- 실용적일 것
- 30분 안에 구현 가능할 것
- 독립적으로 실행 가능할 것
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
            "category": category,
            "complexity": complexity,
        }
