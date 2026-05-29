# =============================================================
#  notebook.py  ―  오답노트 (실수 기록 + 의미 검색)
#                  (새 라이브러리 google-genai 버전)
# -------------------------------------------------------------
#  하는 일:
#   - AI가 과거에 했던 실수를 파일(mistakes.json)에 저장합니다.
#   - 새 작업을 시작할 때, 지금 작업과 "의미가 비슷한" 과거 실수를
#     찾아서 "이거 반복하지 마"라고 알려줄 수 있게 해 줍니다.
#
#  임베딩이란?
#   글의 "의미"를 숫자 목록(벡터)으로 바꾼 것. 의미가 비슷한 글끼리는
#   숫자도 비슷해져서, 단어가 정확히 같지 않아도 비슷한 내용을 찾습니다.
#
#  *** 라이브러리 변경 ***
#    옛: genai.embed_content(...)["embedding"]
#    새: client.models.embed_content(...).embeddings
# =============================================================

import json
from pathlib import Path
from datetime import datetime

import numpy as np
from google import genai
from google.genai import types

from config import GOOGLE_API_KEY, MODELS, NOTEBOOK_PATH

# 새 방식: Client 객체 생성
client = genai.Client(api_key=GOOGLE_API_KEY)


class MistakeNotebook:
    """과거 실수를 임베딩으로 저장하고 검색하는 오답노트."""

    def __init__(self, path=NOTEBOOK_PATH):
        self.path = Path(path)
        self.load()

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
    #  임베딩 만들기 (새 방식)
    #   글(text)을 받아 의미 벡터(숫자 목록)로 바꿉니다.
    #   task_type: 저장용(DOCUMENT)인지 검색용(QUERY)인지 구분.
    # -------------------------------------------------------------
    def embed(self, text, task_type="RETRIEVAL_DOCUMENT"):
        try:
            result = client.models.embed_content(
                model=MODELS["embedding"],
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type),
            )
            # 새 SDK 는 result.embeddings 에 목록으로 담겨 옵니다.
            # 첫 번째 항목의 .values 가 실제 숫자 벡터입니다.
            emb = result.embeddings[0]
            values = getattr(emb, "values", None)
            if values is None:
                values = emb  # 혹시 형태가 다르면 그대로 사용
            return list(values)
        except Exception as e:
            print(f"  [!] 임베딩 생성 실패: {e}")
            return None

    # -------------------------------------------------------------
    #  실수 추가하기
    # -------------------------------------------------------------
    def add_mistake(self, task, problem, lesson, correct_pattern=""):
        full_text = f"작업: {task}\n문제: {problem}\n교훈: {lesson}"
        embedding = self.embed(full_text)

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
            "usage_count": 0,
        }

        self.data["mistakes"].append(mistake)
        self.save()
        print(f"  [+] 실수 #{mistake['id']} 오답노트에 추가됨")
        return True

    # -------------------------------------------------------------
    #  비슷한 실수 검색하기
    # -------------------------------------------------------------
    def search(self, query, top_k=5, threshold=0.65):
        if not self.data["mistakes"]:
            return []

        query_vec = self.embed(query, "RETRIEVAL_QUERY")
        if query_vec is None:
            return []

        query_vec = np.array(query_vec)

        similarities = []
        for mistake in self.data["mistakes"]:
            mistake_vec = np.array(mistake["embedding"])
            # 코사인 유사도: 두 벡터가 얼마나 같은 방향인지 (1에 가까울수록 비슷)
            similarity = np.dot(query_vec, mistake_vec) / (
                np.linalg.norm(query_vec) * np.linalg.norm(mistake_vec)
            )
            similarities.append((mistake, similarity))

        similarities.sort(key=lambda x: x[1], reverse=True)
        relevant = [
            (m, float(s)) for m, s in similarities[:top_k] if s > threshold
        ]

        for mistake, _ in relevant:
            mistake["usage_count"] += 1
        self.save()

        return relevant

    # -------------------------------------------------------------
    #  프롬프트용 텍스트로 변환
    # -------------------------------------------------------------
    def format_for_prompt(self, query):
        relevant = self.search(query)

        if not relevant:
            return ""

        text = "## 과거 실수 (반복 금지!)\n\n"
        for i, (mistake, similarity) in enumerate(relevant, 1):
            text += f"### 실수 {i} (유사도: {similarity:.2f})\n"
            text += f"- 작업: {mistake['task']}\n"
            text += f"- 문제: {mistake['problem']}\n"
            text += f"- 교훈: {mistake['lesson']}\n\n"

        return text
