# =============================================================
#  test_flash.py  ―  "3.5 Flash 모델이 진짜 작동하나?" 확인용
#                    (배치 재처리 = 4점 미만 살리기 에 쓸 모델 점검)
# -------------------------------------------------------------
#  이 파일은 시스템의 일부가 아닙니다.
#  배치 재처리를 붙이기 "전에" 딱 한 번, 내 키로 이 모델을
#  부를 수 있는지만 확인하는 용도입니다.
#
#  확인하는 것:
#    1) .env 의 API 키를 제대로 읽었는가?
#    2) 3.5 Flash 모델이 실제로 응답하는가?
#    3) (참고) 입력/출력 토큰이 얼마나 드는가?
#
#  실행:  python3 test_flash.py
#
#  *** 중요 ***
#    3.5 Flash 는 무료 티어 목록에 없을 수 있습니다(= 결제 필요일 가능성).
#    실패하면 아래 [해석] 부분을 꼭 확인하세요.
# =============================================================

from google import genai            # 새 라이브러리 (google-genai)
from config import GOOGLE_API_KEY


# -------------------------------------------------------------
#  점검할 모델 문자열.
#   AI Studio 콘솔에 보이는 정확한 이름과 다르면 이 한 줄만 고치세요.
# -------------------------------------------------------------
MODEL_ID = "gemini-3.5-flash"


# -------------------------------------------------------------
#  키가 비어있지 않은지 먼저 확인.
# -------------------------------------------------------------
if not GOOGLE_API_KEY:
    print("[X] API 키를 못 읽었습니다.")
    print("   -> .env 파일에 GOOGLE_API_KEY=... 가 있는지 확인하세요.")
    raise SystemExit(1)

print(f"[KEY] 키 읽기 성공 (앞 6자리: {GOOGLE_API_KEY[:6]}...)\n")

# 새 방식: Client 객체를 하나 만들어 재사용합니다.
client = genai.Client(api_key=GOOGLE_API_KEY)


# -------------------------------------------------------------
#  실제로 모델을 한 번 호출해 봅니다.
# -------------------------------------------------------------
print(f"테스트 중: {MODEL_ID}")
try:
    response = client.models.generate_content(
        model=MODEL_ID,
        contents="Say OK",
    )
    text = (response.text or "").strip()
    print(f"  [OK] 성공 - 응답: {text[:40]}")

    # 토큰 사용량이 응답에 있으면 같이 보여줍니다(비용 가늠용).
    meta = getattr(response, "usage_metadata", None)
    if meta is not None:
        in_tok = getattr(meta, "prompt_token_count", 0) or 0
        out_tok = getattr(meta, "candidates_token_count", 0) or 0
        print(f"  [TOKEN] 입력 {in_tok} / 출력 {out_tok}")

    print("\n[DONE] 이 모델을 쓸 수 있습니다. 배치 재처리에 사용 가능.")

except Exception as e:
    print(f"  [X] 실패 - {e}\n")
    print("[해석]")
    print("  - 429 / quota / rate limit   -> 하루 한도 초과(태평양 자정에 리셋)")
    print("  - 404 / not found            -> 모델 이름이 틀림. MODEL_ID 를 콘솔의 정확한 이름으로 고치세요.")
    print("  - permission / billing / 403 -> 무료 티어에 없는 모델일 수 있음(결제 필요할 수 있음)")
    raise SystemExit(1)
