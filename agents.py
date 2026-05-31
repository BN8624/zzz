# =============================================================
#  agents.py  ―  AI 모델 호출 담당 (새 라이브러리 google-genai 버전)
# -------------------------------------------------------------
#  하는 일:
#   - Google AI Studio 로 26B / 31B 모델을 호출합니다.
#   - 폴백(다른 API로 넘어가기)은 없습니다. 단순하게 갑니다.
#   - 실패하면 잠깐 쉬었다가 다시 시도(재시도)만 합니다.
#   - 호출할 때마다 입력/출력 토큰 수를 로그에 남기고,
#     오늘 몇 번 호출했는지 세어 둡니다(나중에 대시보드용).
#
#  *** 라이브러리 변경 ***
#    옛: google-generativeai (genai.configure / GenerativeModel)
#    새: google-genai        (genai.Client / client.models...)
# =============================================================

import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

# 사용량 카운터의 "오늘" 기준은 태평양시(PST/PDT)입니다.
#  구글 무료 API 한도는 태평양시 자정에 리셋되므로,
#  카운터도 같은 시점에 0으로 돌아가야 한도와 정확히 맞습니다.
#  (서버 시계가 UTC 든 무엇이든 상관없이 항상 태평양시 날짜로 계산)
_PACIFIC = ZoneInfo("America/Los_Angeles")

def _pacific_today():
    """현재 태평양시 기준 날짜를 'YYYY-MM-DD' 문자열로 돌려줍니다."""
    return datetime.now(_PACIFIC).date().isoformat()

from google import genai           # 새 라이브러리

from config import GOOGLE_API_KEY, MODELS, RATE_LIMIT

# 새 방식: Client 객체를 하나 만들어 두고 계속 재사용합니다.
client = genai.Client(api_key=GOOGLE_API_KEY)


# =============================================================
#  [1] 사용량 카운터
# -------------------------------------------------------------
#  오늘 각 모델을 몇 번 호출했는지, 토큰을 얼마나 썼는지 누적.
#  - 프로그램을 껐다 켜면 초기화됩니다(메모리에만 있음).
#  - 날짜가 바뀌면 자동으로 0으로 리셋합니다.
#  나중에 대시보드에서 이 값을 꺼내 보여줄 수 있습니다.
# =============================================================
#  ★ 영속화: 카운터를 파일(usage.json)에도 저장합니다.
#    왜? 카운터가 메모리에만 있으면 프로그램을 재시작할 때 0 으로 돌아가,
#    "오늘 이미 1400회 썼다"는 사실을 잊고 처음부터 다시 셉니다.
#    그러면 한도 안전선(1450)이 뚫려 429 가 터집니다.
#    그래서 호출할 때마다 파일에 기록하고, 시작할 때 파일에서 불러옵니다.
#    → 재시작해도 "오늘(태평양시 기준) 누적 호출수"가 그대로 유지됩니다.
USAGE_PATH = "usage.json"


def _blank_usage():
    """비어 있는(0회) 카운터 한 벌을 만들어 돌려줍니다."""
    return {
        "date": _pacific_today(),   # 태평양시 기준 오늘
        "26b": {"calls": 0, "in": 0, "out": 0},
        "31b": {"calls": 0, "in": 0, "out": 0},
        "flash_lite": {"calls": 0, "in": 0, "out": 0},
        "last_call_at": None,   # 마지막으로 모델을 호출한 시각(대시보드 가동등용)
    }


def _load_usage():
    """시작 시 파일에서 카운터를 불러옵니다.
       - 파일이 없거나 깨졌으면 빈 카운터로 시작.
       - 파일의 날짜가 '오늘(태평양시)'과 다르면 → 어제 것이므로 0 으로 새로 시작.
    """
    try:
        with open(USAGE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 날짜가 바뀌었으면(어제 파일이면) 빈 카운터로.
        if data.get("date") != _pacific_today():
            return _blank_usage()
        # 형식이 온전한지 최소 확인 후 사용.
        for k in ("26b", "31b", "flash_lite"):
            if k not in data:
                data[k] = {"calls": 0, "in": 0, "out": 0}
        if "last_call_at" not in data:
            data["last_call_at"] = None
        return data
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return _blank_usage()


def _save_usage():
    """현재 카운터를 파일에 저장합니다(원자적 쓰기: 임시파일→교체).
       중간에 꺼져도 파일이 깨지지 않도록 임시파일에 쓴 뒤 바꿔치기합니다."""
    try:
        tmp = USAGE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(USAGE, f, ensure_ascii=False)
        os.replace(tmp, USAGE_PATH)
    except Exception:
        # 저장 실패는 치명적이지 않으므로 조용히 넘어갑니다(다음 호출 때 다시 시도).
        pass


# 프로그램 시작 시 파일에서 불러와 메모리 카운터를 채웁니다.
USAGE = _load_usage()


def _reset_if_new_day():
    """날짜(태평양시)가 바뀌었으면 카운터를 0으로 초기화하고 파일도 갱신합니다."""
    today = _pacific_today()
    if USAGE["date"] != today:
        USAGE["date"] = today
        USAGE["26b"] = {"calls": 0, "in": 0, "out": 0}
        USAGE["31b"] = {"calls": 0, "in": 0, "out": 0}
        USAGE["flash_lite"] = {"calls": 0, "in": 0, "out": 0}
        _save_usage()


def _record_usage(key, in_tokens, out_tokens):
    """한 번 호출한 결과(토큰 수)를 카운터에 더하고, 파일에 즉시 저장합니다."""
    _reset_if_new_day()
    USAGE[key]["calls"] += 1
    USAGE[key]["in"] += in_tokens
    USAGE[key]["out"] += out_tokens
    # 마지막 호출 시각 기록 → 대시보드가 "지금 가동 중"을 판단하는 근거.
    #  (저장이 아니라 '호출'을 기준으로 해야 4점 미만 작업 중에도 초록불이 됨)
    USAGE["last_call_at"] = datetime.now().isoformat()
    # 호출할 때마다 파일에 저장 → 재시작해도 누적값 유지(한도 안전선의 토대).
    _save_usage()


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
        self.min_interval = min_interval
        self.last_call = 0

    def wait(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()


rate_limiter = RateLimiter(RATE_LIMIT["min_interval_seconds"])


# =============================================================
#  [3] 실제 호출 함수 (내부용)
# -------------------------------------------------------------
#  모델 하나를 호출하고, 실패하면 재시도합니다.
#  재시도 사이에는 점점 더 길게 쉽니다(30초, 60초 ...).
# =============================================================
def _call(model_id, usage_key, prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            # (1) 속도 제한 지키기
            rate_limiter.wait()

            # (2) 모델 호출 (새 방식)
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
            )

            # (3) 토큰 사용량 꺼내기
            #     응답 안 usage_metadata 에 들어있습니다.
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
            print(f"  [!] {usage_key.upper()} 호출 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait_sec = (attempt + 1) * 30   # 30초, 60초 ...
                print(f"      {wait_sec}초 후 재시도합니다.")
                time.sleep(wait_sec)
            else:
                raise   # 끝까지 실패하면 에러를 위로 던집니다.

    return None


# =============================================================
#  [4] 바깥에서 쓰는 함수 두 개
# -------------------------------------------------------------
#   - call_gemma_26b: 지시 / 디버깅 / 주제생성 에 사용
#   - call_gemma_31b: 코딩 / 검토 / 평가 에 사용
# =============================================================
def call_gemma_26b(prompt):
    return _call(MODELS["gemma_26b"], "26b", prompt)


def call_gemma_31b(prompt):
    return _call(MODELS["gemma_31b"], "31b", prompt)


# 3.1 Flash-Lite 호출.  ★ 현재 미사용 (함수만 보존)
#  과거엔 교훈 정제를 맡았으나, 코드의 구체적 결함을 짚기엔 약해
#  추상적 교훈만 나왔습니다. 그래서 교훈 정제는 코드 분석에 강한 31B 로
#  옮겼습니다(orchestrator.record_lesson).
#  이 함수는 지우지 않고 남겨 둡니다 — 나중에 "요약·분류·태깅" 같은
#  루프 밖 가벼운 단발 작업이 생기면 그때 재활용하기 위함입니다.
#  (26B/31B 와 한도가 따로라, 여기 호출은 그 두 모델 페이싱과 무관)
def call_flash_lite(prompt):
    return _call(MODELS["flash_lite"], "flash_lite", prompt)
