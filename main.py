# =============================================================
#  main.py  ―  진입점 (모든 것을 합쳐서 실행)
# -------------------------------------------------------------
#  이 파일 하나만 실행하면:
#    (1) 백그라운드에서 자동 코딩 루프가 돌고
#    (2) 동시에 대시보드(폰 접속용)도 켜집니다.
#
#  실행:  python3 main.py
#  폰 접속:  http://<서버IP>:5000
#
#  자동 루프 정책 (방식 B = "쭉 돌리다 한도 닿으면 정지"):
#    - 작업을 쉬지 않고 계속 돌립니다.
#    - 단, 오늘 호출 수가 안전선(SAFE_LIMIT)에 닿으면 멈춥니다.
#    - 태평양 시간(PST) 날짜가 바뀌면 한도가 리셋되므로,
#      그때 카운터도 0이 되어 다시 돌기 시작합니다.
#    - 작업 하나가 에러로 터져도, 잡아서 로그만 남기고 계속 갑니다.
#      (에러 하나에 전체가 죽지 않도록 = 에러 격리)
# =============================================================

import sys
import time
import threading
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo   # 시간대(태평양시간) 계산용 (파이썬 표준)

from orchestrator import CodeAgent
from topic_generator import AutoTopicGenerator
from database import CodeDatabase
from agents import get_usage
from logger import log_task_result, console
from config import DAILY_LIMIT, SCHEDULING, REPROCESS


# -------------------------------------------------------------
#  안전선 설정
# -------------------------------------------------------------
#  구글 무료 한도는 모델당 하루 1,500회입니다.
#  딱 1,500 까지 쓰면 위험하므로, 그 전에 멈출 "안전선"을 둡니다.
#  (콘솔 표시와 실제 카운트가 어긋날 수 있어 보수적으로 잡음)
SAFE_LIMIT = 1450

#  태평양 시간대 (구글 한도 리셋 기준)
PACIFIC = ZoneInfo("America/Los_Angeles")

#  작업 사이 최소 대기(초). 폭주만 막는 용도로 짧게 둡니다.
#  쭉 돌리되, 호출이 너무 몰리지 않게 하는 최소한의 간격.
MIN_GAP = 5

#  마지막으로 자동정리(cleanup)를 돈 날짜(태평양 기준). 하루 1번만 돌게.
_last_cleanup_day = None

#  마지막으로 배치 재처리를 돈 시각(time.time() 초). 주기 계산용.
_last_reprocess_at = None


def pacific_today():
    """지금 '태평양 시간 기준' 날짜를 돌려줍니다. (한도 리셋 기준일)"""
    return datetime.now(PACIFIC).date().isoformat()


def over_limit():
    """
    오늘 26B 또는 31B 중 하나라도 안전선을 넘었는지 확인합니다.
    둘 중 더 많이 쓴 쪽이 기준입니다(먼저 한도에 닿는 쪽).
    넘었으면 True (= 멈춰야 함).
    """
    usage = get_usage()
    used = max(usage["26b"]["calls"], usage["31b"]["calls"])
    return used >= SAFE_LIMIT


def maybe_cleanup(db):
    """태평양 날짜가 바뀌었으면 하루 한 번 자동정리를 돌립니다."""
    global _last_cleanup_day
    today = pacific_today()
    if _last_cleanup_day != today:
        _last_cleanup_day = today
        db.daily_cleanup()


def maybe_reprocess(db, agent):
    """
    일정 시간(REPROCESS interval)마다 3점대 결과를 보완(재처리)합니다.
    한 번에 max_per_batch 개만 처리해 3.5 Flash 한도(RPD)를 아낍니다.
    프로그램 시작 직후 1회 돌고, 그 뒤로는 interval_hours 마다 돕니다.
    """
    global _last_reprocess_at
    now = time.time()
    interval = REPROCESS["interval_hours"] * 3600

    # 아직 주기가 안 됐으면 그냥 돌아갑니다.
    if _last_reprocess_at is not None and (now - _last_reprocess_at) < interval:
        return
    _last_reprocess_at = now

    # 살릴 후보(3점대 + 아직 재처리 안 함)를 가져옵니다.
    candidates = db.get_reprocess_candidates(REPROCESS["max_per_batch"])
    if not candidates:
        return  # 후보가 없으면 끝.

    for rec in candidates:
        try:
            result = agent.reprocess_one(rec)
            if result is None:
                # 보완 실패(위험코드 등) → "재처리함"만 표시해 원래대로 폐기되게.
                db.apply_reprocess(
                    rec["id"], rec["code"], rec["score"], rec["summary"]
                )
                continue
            new_code, new_score, new_summary = result
            db.apply_reprocess(rec["id"], new_code, new_score, new_summary)
            mark = "⬆️ 승격" if new_score >= 4.0 else "유지(곧 폐기)"
            console.print(
                f"[cyan]♻️  #{rec['id']} 재처리: "
                f"{rec['score']:.1f} → {new_score:.1f}  {mark}[/cyan]"
            )
        except Exception as e:
            console.print(f"[red]⚠️ 재처리 실패 #{rec['id']}: {e}[/red]")


# =============================================================
#  자동 코딩 루프 (백그라운드 스레드에서 돎)
# =============================================================
def auto_loop():
    topic_gen = AutoTopicGenerator()
    agent = CodeAgent()
    db = CodeDatabase()

    console.print("[bold green]🏭 자동 코딩 루프 시작[/bold green]")

    while True:
        try:
            # (0) 하루 한 번 자동정리 (날짜 바뀌었으면)
            maybe_cleanup(db)

            # (1) 한도 안전선에 닿았으면 잠시 쉬고 다시 확인
            #     태평양 자정이 지나면 카운터가 리셋돼서 다시 풀립니다.
            if over_limit():
                console.print(
                    "[bold yellow]⏸  오늘 한도 도달 — 리셋(태평양 자정)까지 대기[/bold yellow]"
                )
                time.sleep(600)   # 10분 쉬고 다시 확인
                continue

            # (1-2) 배치 재처리: 주기가 됐으면 3점대 결과를 보완.
            #       (한도를 통과했을 때만 돌려 31B 재평가 부담을 줄임)
            maybe_reprocess(db, agent)

            # (2) 주제 생성 → 6단계 실행 → 저장
            topic_info = topic_gen.generate()
            result = agent.run(topic_info["topic"])
            result_id = db.save(topic_info["topic"], result)

            # 저장됐는지(4점 이상) 결과 표시
            log_task_result(saved=result_id is not None, result_id=result_id)

            # (3) 폭주 방지용 최소 간격
            time.sleep(MIN_GAP)

        except Exception as e:
            # 작업 하나가 통째로 실패해도 전체는 죽지 않게.
            console.print(f"[bold red]⚠️ 작업 실패(건너뜀): {e}[/bold red]")
            traceback.print_exc()
            time.sleep(30)   # 잠깐 쉬고 다음 작업으로


# =============================================================
#  실행 진입점
# =============================================================
def main():
    # (1) 자동 루프를 백그라운드 스레드로 시작.
    #     daemon=True → 메인이 끝나면 같이 종료.
    t = threading.Thread(target=auto_loop, daemon=True)
    t.start()

    # (2) 대시보드(Flask)를 켭니다. 이게 메인 프로세스를 붙잡아 둡니다.
    #     폰에서 http://<서버IP>:5000 으로 접속.
    from dashboard import app
    console.print("[bold cyan]🖥  대시보드 시작 → http://<서버IP>:5000[/bold cyan]")
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
