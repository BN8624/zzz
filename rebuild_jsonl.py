# =============================================================
#  rebuild_jsonl.py  ―  collected_meta.json 의 source_url 로
#                       README 를 다시 받아 embedding_requests.jsonl 복원
# -------------------------------------------------------------
#  jsonl 이 실수로 100건으로 덮였지만 meta(4942건)는 살아있어서,
#  meta 의 source_url 로 README 본문만 다시 내려받아 복원합니다.
#  (검색 단계 불필요 → 빠름. raw 다운로드는 한도 없음)
#
#  실행:  python3 rebuild_jsonl.py
# =============================================================

import json
import time
import requests

META_JSON = "collected_meta.json"
OUT_JSONL = "embedding_requests.jsonl"
README_MAX_CHARS = 8000

meta = json.load(open(META_JSON, encoding="utf-8"))
print(f"meta {len(meta)}건 → README 재다운로드 시작\n")

reqs = []
ok = fail = 0
for i, m in enumerate(meta):
    url = m.get("source_url", "")
    key = m["key"]
    if not url:
        fail += 1
        continue
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and r.text.strip():
            text = r.text[:README_MAX_CHARS]
            reqs.append({"key": key,
                         "request": {"content": {"parts": [{"text": text}]}}})
            ok += 1
        else:
            fail += 1
    except Exception:
        fail += 1
    if (i + 1) % 200 == 0:
        print(f"  ...{i+1}/{len(meta)}  (성공 {ok}, 실패 {fail})")
    time.sleep(0.05)

with open(OUT_JSONL, "w", encoding="utf-8") as f:
    for req in reqs:
        f.write(json.dumps(req, ensure_ascii=False) + "\n")

print(f"\n✅ 복원 완료: {OUT_JSONL}  {ok}건 (실패 {fail})")
