# =============================================================
#  test_key.py  ―  "키와 모델이 진짜 작동하나?" 확인용 테스트
#                  (새 라이브러리 google-genai 버전)
# -------------------------------------------------------------
#  이 파일은 시스템의 일부가 아닙니다. 시작 전 한 번만 확인용입니다.
#    1) .env 의 API 키를 제대로 읽었는가?
#    2) 26B 모델이 실제로 응답하는가?
#    3) 31B 모델이 실제로 응답하는가?
#
#  실행:  python3 "test key.py"   (파일명에 공백 있으면 따옴표 필요)
#  결과:  두 모델 모두 "OK 성공" 이면 다음 단계로.
#
#  *** 구글이 라이브러리를 바꿨습니다 ***
#    옛: google-generativeai  (genai.configure / GenerativeModel)
#    새: google-genai         (genai.Client / client.models...)
#    이 파일은 "새 방식"으로 작성됐습니다.
# =============================================================

from google import genai            # 새 라이브러리
from config import GOOGLE_API_KEY, MODELS


# -------------------------------------------------------------
#  키가 비어있지 않은지 먼저 확인.
# -------------------------------------------------------------
if not GOOGLE_API_KEY:
    print("[X] API 키를 못 읽었습니다.")
    print("   -> .env 파일에 GOOGLE_API_KEY=... 가 있는지 확인하세요.")
    raise SystemExit(1)

print(f"[KEY] 키 읽기 성공 (앞 6자리: {GOOGLE_API_KEY[:6]}...)\n")

# 새 방식: Client 객체를 하나 만들어 두고 재사용합니다.
client = genai.Client(api_key=GOOGLE_API_KEY)


# -------------------------------------------------------------
#  모델 하나를 실제로 호출해보는 함수.
# -------------------------------------------------------------
def test_one(label, model_id):
    print(f"테스트 중: {label} ({model_id})")
    try:
        response = client.models.generate_content(
            model=model_id,
            contents="Say OK",
        )
        text = (response.text or "").strip()
        print(f"  [OK] 성공 - 응답: {text[:40]}\n")
        return True
    except Exception as e:
        print(f"  [X] 실패 - {e}\n")
        return False


# -------------------------------------------------------------
#  26B / 31B 둘 다 테스트.
# -------------------------------------------------------------
ok_26 = test_one("26B", MODELS["gemma_26b"])
ok_31 = test_one("31B", MODELS["gemma_31b"])


# -------------------------------------------------------------
#  최종 결과 안내.
# -------------------------------------------------------------
if ok_26 and ok_31:
    print("[DONE] 두 모델 모두 정상! 다음 단계로 진행하세요.")
else:
    print("[WARN] 실패한 모델이 있습니다. 위 에러 메시지를 확인하세요.")
    print("   - 429 면 -> 하루 한도 초과(태평양 자정에 리셋)")
    print("   - 그 외면 -> 모델 이름 또는 키 권한 문제")
