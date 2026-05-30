# =============================================================
#  batch_test.py  ―  배치 임베딩 테스트
# -------------------------------------------------------------
#  하는 일:
#    collect_test.py 가 만든 embedding_requests.jsonl 을
#    Gemini Batch API 로 임베딩하고, 결과를 받아옵니다.
#
#  *** 서버용 구조 (폴링으로 24시간 붙잡지 않음) ***
#    배치는 최대 24시간 걸릴 수 있어, 한 번에 끝까지 기다리지 않고
#    명령을 나눠서 씁니다:
#
#    1) 던지기 :  python3 batch_test.py submit
#       → 작업을 만들고, 작업이름을 batch_job.txt 에 저장하고 끝.
#
#    2) 확인   :  python3 batch_test.py check
#       → 지금 상태만 출력(끝났는지/도는중인지). 끝났으면 결과도 받음.
#       → 아직이면 나중에 다시 check 하면 됩니다(붙잡고 안 기다림).
#
#  무료/유료: 배치 임베딩은 결제 활성화된 키가 필요할 수 있습니다.
#            (이 키는 크레딧 있는 키로 확인됨)
# =============================================================

import sys
import json

from google import genai
from config import GOOGLE_API_KEY, MODELS

client = genai.Client(api_key=GOOGLE_API_KEY)

INPUT_JSONL = "embedding_requests.jsonl"   # collect_test.py 결과
JOB_FILE = "batch_job.txt"                 # 작업이름 저장(던지기↔확인 연결용)
OUTPUT_JSONL = "embeddings_out.jsonl"      # 최종 임베딩 결과
EMBED_MODEL = MODELS["embedding"]          # config 와 같은 모델! (일관성 필수)


# -------------------------------------------------------------
#  1) 던지기 ― 입력 파일을 올리고 배치 작업을 만듭니다.
# -------------------------------------------------------------
def submit():
    print(f"[submit] 모델: {EMBED_MODEL}")
    print(f"[submit] 입력 파일 업로드 중: {INPUT_JSONL}")
    uploaded = client.files.upload(file=INPUT_JSONL)
    print(f"    업로드됨: {uploaded.name}")

    print("[submit] 배치 임베딩 작업 생성 중...")
    job = client.batches.create_embeddings(
        model=EMBED_MODEL,
        src={"file_name": uploaded.name},
        config={"display_name": "research-db-test"},
    )
    # 작업이름을 파일에 저장 → 나중에 check 가 이걸 읽어 상태를 봅니다.
    with open(JOB_FILE, "w") as f:
        f.write(job.name)

    print(f"    작업 생성됨: {job.name}")
    print(f"    상태: {job.state.name if hasattr(job.state,'name') else job.state}")
    print(f"\n던졌습니다. 잠시 후(보통 몇 분~수십 분) 아래로 확인하세요:")
    print(f"    python3 batch_test.py check")


# -------------------------------------------------------------
#  2) 확인 ― 저장해둔 작업이름으로 상태를 조회. 끝났으면 결과 다운로드.
# -------------------------------------------------------------
def check():
    try:
        with open(JOB_FILE) as f:
            job_name = f.read().strip()
    except FileNotFoundError:
        print("[check] 먼저 submit 을 실행하세요. (batch_job.txt 없음)")
        return

    job = client.batches.get(name=job_name)
    state = job.state.name if hasattr(job.state, "name") else str(job.state)
    print(f"[check] 작업: {job_name}")
    print(f"[check] 상태: {state}")

    if state == "JOB_STATE_SUCCEEDED":
        print("[check] 완료! 결과 내려받는 중...")
        # 결과 파일을 다운로드. (입력과 같은 순서, JSONL)
        content = client.files.download(file=job.dest.file_name).decode("utf-8")

        # 메타와 합쳐 보기 좋게 저장.
        try:
            with open("collected_meta.json", encoding="utf-8") as f:
                meta = {m["key"]: m for m in json.load(f)}
        except FileNotFoundError:
            meta = {}

        n, dims = 0, None
        with open(OUTPUT_JSONL, "w", encoding="utf-8") as out:
            for line in content.splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                out.write(line + "\n")
                n += 1
                # 첫 줄에서 벡터 차원만 한 번 확인(형식 점검용).
                if dims is None:
                    dims = _peek_dims(row)
        print(f"    저장: {OUTPUT_JSONL}  ({n}건, 벡터 차원 ≈ {dims})")
        print(f"    다음 단계: 이 벡터들을 research.db(sqlite-vec)에 넣으면 됩니다.")

    elif state in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"):
        print(f"[check] 작업이 끝나지 못했습니다: {state}")
        err = getattr(job, "error", None)
        if err:
            print(f"    오류: {err}")
    else:
        print("[check] 아직 처리 중입니다. 잠시 후 다시 check 하세요.")


def _peek_dims(row):
    """결과 한 줄에서 임베딩 벡터의 길이(차원)를 최대한 안전하게 찾아봅니다.
       SDK 버전에 따라 응답 구조가 조금씩 달라서 여러 경로를 시도합니다."""
    try:
        # 흔한 형태: row["response"]["embedding"]["values"]
        resp = row.get("response", row)
        emb = resp.get("embedding") or resp.get("embeddings")
        if isinstance(emb, list) and emb and isinstance(emb[0], dict):
            emb = emb[0]
        vals = emb.get("values") if isinstance(emb, dict) else emb
        return len(vals) if vals else "?"
    except Exception:
        return "?"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "submit":
        submit()
    elif cmd == "check":
        check()
    else:
        print("사용법:")
        print("  python3 batch_test.py submit   # 배치 던지기")
        print("  python3 batch_test.py check    # 상태확인/결과받기")
