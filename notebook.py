# =============================================================
#  notebook.py  ―  오답노트 (실수 기록 + 의미 검색)
# -------------------------------------------------------------
#  하는 일:
#   - AI가 과거에 했던 실수를 파일(mistakes.json)에 저장합니다.
#   - 새 작업을 시작할 때, 지금 작업과 "의미가 비슷한" 과거 실수를
#     찾아서 "이거 반복하지 마"라고 알려줄 수 있게 해 줍니다.
#
#  핵심 개념 ― 임베딩(embedding)이란?
#   글의 "의미"를 숫자 목록(벡터)으로 바꾼 것입니다.
#   의미가 비슷한 글끼리는 숫자도 비슷해집니다.
#   그래서 단어가 정확히 같지 않아도 "비슷한 내용"을 찾아낼 수 있습니다.
#   (예: "파일을 못 읽음" 과 "파일 열기 실패" 를 비슷하다고 인식)
#
#  사용 모델: config 의 MODELS["embedding"] (= Gemini Embedding 2)
# =============================================================

import json
from pathlib import Path
from datetime import datetime

import numpy as np
import google.generativeai as genai

from config import GOOGLE_API_KEY, MODELS, NOTEBOOK_PATH

# 라이브러리에 API 키 등록
genai.configure(api_key=GOOGLE_API_KEY)


class MistakeNotebook:
    """과거 실수를 임베딩으로 저장하고 검색하는 오답노트."""

    def __init__(self, path=NOTEBOOK_PATH):
        self.path = Path(path)
        self.load()   # 시작할 때 기존 기록을 불러옵니다.

    # -------------------------------------------------------------
    #  파일 읽기/쓰기
    # -------------------------------------------------------------
    def load(self):
        """저장된 오답노트 파일을 읽어옵니다. 없으면 새로 만듭니다."""
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {"mistakes": []}
            self.save()

    def save(self):
        """현재 오답노트를 파일에 저장합니다."""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    # -------------------------------------------------------------
    #  임베딩 만들기
    #   글(text)을 받아 의미 벡터(숫자 목록)로 바꿉니다.
    #   task_type: 저장용(DOCUMENT)인지 검색용(QUERY)인지 구분.
    # -------------------------------------------------------------
    def embed(self, text, task_type="RETRIEVAL_DOCUMENT"):
        try:
            result = genai.embed_content(
                model=MODELS["embedding"],
                content=text,
                task_type=task_type,
            )
            return result["embedding"]
        except Exception as e:
            print(f"  ⚠️ 임베딩 생성 실패: {e}")
            return None

    # -------------------------------------------------------------
    #  실수 추가하기
    #   task   : 어떤 작업이었나
    #   problem: 무슨 문제가 있었나
    #   lesson : 그래서 얻은 교훈
    # -------------------------------------------------------------
    def add_mistake(self, task, problem, lesson, correct_pattern=""):
        full_text = f"작업: {task}\n문제: {problem}\n교훈: {lesson}"
        embedding = self.embed(full_text)

        # 임베딩을 못 만들면 저장하지 않습니다(검색이 안 되므로).
        if embedding is None:
            return False

        mistake = {
            "id": len(self.data["mistakes"]) + 1,
            "task": task,
            "problem": problem,
            "lesson": lesson,
            "correct_pattern": correct_pattern,
            "embedding": embedding,
            "added_at": datetime.now().isoformat(),
            "usage_count": 0,   # 이 실수가 검색에 몇 번 쓰였는지
        }

        self.data["mistakes"].append(mistake)
        self.save()
        print(f"  ✅ 실수 #{mistake['id']} 오답노트에 추가됨")
        return True

    # -------------------------------------------------------------
    #  비슷한 실수 검색하기
    #   query    : 지금 하려는 작업 설명
    #   top_k    : 최대 몇 개까지 찾을지
    #   threshold: 유사도가 이 값보다 높은 것만 (0~1, 클수록 엄격)
    # -------------------------------------------------------------
    def search(self, query, top_k=5, threshold=0.65):
        # 저장된 실수가 없으면 검색할 것도 없습니다.
        if not self.data["mistakes"]:
            return []

        # 검색어를 임베딩으로 변환(검색용 타입)
        query_vec = self.embed(query, "RETRIEVAL_QUERY")
        if query_vec is None:
            return []

        query_vec = np.array(query_vec)

        # 저장된 모든 실수와 "유사도"를 계산합니다.
        #  - 코사인 유사도: 두 벡터가 얼마나 같은 방향인지 (1에 가까울수록 비슷)
        similarities = []
        for mistake in self.data["mistakes"]:
            mistake_vec = np.array(mistake["embedding"])
            similarity = np.dot(query_vec, mistake_vec) / (
                np.linalg.norm(query_vec) * np.linalg.norm(mistake_vec)
            )
            similarities.append((mistake, similarity))

        # 유사도가 높은 순으로 정렬
        similarities.sort(key=lambda x: x[1], reverse=True)

        # 상위 top_k 중에서, 기준점(threshold)을 넘는 것만 추립니다.
        relevant = [
            (m, float(s)) for m, s in similarities[:top_k] if s > threshold
        ]

        # 검색에 쓰인 실수는 usage_count 를 1 올립니다.
        for mistake, _ in relevant:
            mistake["usage_count"] += 1
        self.save()

        return relevant

    # -------------------------------------------------------------
    #  프롬프트용 텍스트로 변환
    #   검색된 실수들을, AI에게 보여줄 문장 형태로 만듭니다.
    #   이 결과를 코딩/지시 프롬프트에 끼워 넣습니다.
    # -------------------------------------------------------------
    def format_for_prompt(self, query):
        relevant = self.search(query)

        # 관련 실수가 없으면 빈 문자열(끼워 넣을 게 없음).
        if not relevant:
            return ""

        text = "## ⚠️ 과거 실수 (반복 금지!)\n\n"
        for i, (mistake, similarity) in enumerate(relevant, 1):
            text += f"### 실수 {i} (유사도: {similarity:.2f})\n"
            text += f"- 작업: {mistake['task']}\n"
            text += f"- 문제: {mistake['problem']}\n"
            text += f"- 교훈: {mistake['lesson']}\n\n"

        return text
