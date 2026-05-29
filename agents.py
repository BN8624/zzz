# =============================================================
#  agents.py  ―  AI 모델 호출 담당
# -------------------------------------------------------------
#  하는 일:
#   - Google AI Studio 로 26B / 31B 모델을 호출합니다.
#   - 폴백(다른 API로 넘어가기)은 없습니다. 단순하게 갑니다.
#   - 실패하면 잠깐 쉬었다가 다시 시도(재시도)만 합니다.
#   - 호출할 때마다 입력/출력 토큰 수를 로그에 남기고,
#     오늘 몇 번 호출했는지 세어 둡니다(나중에 대시보드용).
# =============================================================

import time
from datetime import date

import google.generativeai as genai

from config import GOOGLE_API_KEY, MODELS, RATE_LIMIT

# 라이브러리에 API 키를 등록합니다.
genai.configure(api_key=GOOGLE_API_KEY)


# =============================================================
#  [1] 사용량 카운터
# -------------------------------------------------------------
#  오늘 각 모델을 몇 번 호출했는지, 토큰을 얼마나 썼는지
#  여기에 누적해 둡니다.
#  - 프로그램을 껐다 켜면 초기화됩니다(메모리에만 있음).
#  - 날짜가 바뀌면 자동으로 0으로 리셋합니다.
#  나중에 대시보드에서 이 값을 꺼내 보여줄 수 있습니다.
# =============================================================
USAGE = {
    "date": date.today().isoformat(),  # 오늘 날짜(리셋 기준)
    "26b": {"calls": 0, "in": 0, "out": 0},
    "31b": {"calls": 0, "in": 0, "out": 0},
}


def _reset_if_new_day():
    """날짜가 바뀌었으면 카운터를 0으로 초기화합니다."""
    today = date.today().isoformat()
    if USAGE["date"] != today:
        USAGE["date"] = today
        USAGE["26b"] = {"calls": 0, "in": 0, "out": 0}
        USAGE["31b"] = {"calls": 0, "in": 0, "out": 0}


def _record_usage(key, in_tokens, out_tokens):
    """한 번 호출한 결과(토큰 수)를 카운터에 더합니다."""
    _reset_if_new_day()
    USAGE[key]["calls"] += 1
    USAGE[key]["in"] += in_tokens
    USAGE[key]["out"] += out_tokens


def get_usage():
    """현재 사용량을 돌려줍니다(대시보드 등에서 사용)."""
    _reset_if_new_day()
    return USAGE


# =============================================================
#  [2] 호출 속도 제한기 (Rate Limiter)
# -------------------------------------------------------------
#  너무 빠르게 연속 호출하면 분당 한도(RPM)에 걸립니다.
#  그래서 호출과 호출 사이에 최소 몇 초를 강제로 쉽니다.
# =============================================================
class RateLimiter:
    def __init__(self, min_interval):
        self.min_interval = min_interval   # 최소 간격(초)
        self.last_call = 0                 # 마지막 호출 시각

    def wait(self):
        """필요하면 잠깐 멈춰서 간격을 맞춥니다."""
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()


# 설정에서 간격(기본 6초)을 읽어 만듭니다.
rate_limiter = RateLimiter(RATE_LIMIT["min_interval_seconds"])


# =============================================================
#  [3] 실제 호출 함수 (내부용)
# -------------------------------------------------------------
#  모델 하나를 호출하고, 실패하면 재시도합니다.
#  - max_retries: 최대 몇 번까지 다시 시도할지
#  - 재시도 사이에는 점점 더 길게 쉽니다(30초, 60초 ...).
# =============================================================
def _call(model_id, usage_key, prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            # (1) 속도 제한 지키기
            rate_limiter.wait()

            # (2) 모델 호출
            model = genai.GenerativeModel(model_id)
            response = model.generate_content(prompt)

            # (3) 토큰 사용량 꺼내기
            #     응답 안에 usage_metadata 가 들어있습니다.
            #     혹시 없을 때를 대비해 안전하게 0으로 둡니다.
            in_tok, out_tok = 0, 0
            meta = getattr(response, "usage_metadata", None)
            if meta is not None:
                in_tok = getattr(meta, "prompt_token_count", 0) or 0
                out_tok = getattr(meta, "candidates_token_count", 0) or 0

            # (4) 카운터에 기록 + 로그 한 줄 출력
            _record_usage(usage_key, in_tok, out_tok)
            print(f"  [{usage_key.upper()}] 입력 {in_tok}토큰 / 출력 {out_tok}토큰")

            # (5) 모델이 만든 글(텍스트)을 돌려줍니다.
            return response.text

        except Exception as e:
            # 실패한 경우: 마지막 시도가 아니면 쉬었다가 다시.
            print(f"  ⚠️ {usage_key.upper()} 호출 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_sec = (attempt + 1) * 30   # 30초, 60초 ...
                print(f"     {wait_sec}초 후 재시도합니다.")
                time.sleep(wait_sec)
            else:
                # 끝까지 실패하면 에러를 위로 던집니다.
                raise

    return None


# =============================================================
#  [4] 바깥에서 쓰는 함수 두 개
# -------------------------------------------------------------
#  다른 파일(orchestrator 등)은 이 두 함수만 부르면 됩니다.
#   - call_gemma_26b: 지시 / 디버깅 / 주제생성 에 사용
#   - call_gemma_31b: 코딩 / 검토 / 평가 에 사용
# =============================================================
def call_gemma_26b(prompt):
    return _call(MODELS["gemma_26b"], "26b", prompt)


def call_gemma_31b(prompt):
    return _call(MODELS["gemma_31b"], "31b", prompt)
