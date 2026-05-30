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
#  자동 루프 정책 (유동 페이싱 = "호출량에 맞춰 속도를 조절"):
#    - 작업을 계속 돌리되, 작업마다 "쓴 호출 수 × 정해진 초"만큼 시간을
#      확보하도록 끝에서 쉽니다. 그러면 하루 호출 수가 자연히 한도 아래로
#      갇힙니다. (RPD 를 넘은 뒤 막는 게 아니라, 처음부터 못 넘게 함)
#    - 이 방식은 카운터가 리셋되거나 반복 횟수가 들쭉날쭉해도 안전합니다.
#    - over_limit() 안전선은 '보조 방어선'으로 남겨둡니다(이중 안전).
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
from config import DAILY_LIMIT, PACING


# -------------------------------------------------------------
#  안전선 설정
# -------------------------------------------------------------
#  구글 무료 한도는 모델당 하루 1,500회입니다.
#  딱 1,500 까지 쓰면 위험하므로, 그 전에 멈출 "안전선"을 둡니다.
#  (콘솔 표시와 실제 카운트가 어긋날 수 있어 보수적으로 잡음)
SAFE_LIMIT = 1450

#  태평양 시간대 (구글 한도 리셋 기준)
PACIFIC = ZoneInfo("America/Los_Angeles")

#  유동 페이싱: 호출 1회당 확보할 시간(초). config 에서 가져옵니다.
#   작업이 쓴 호출 수에 이 값을 곱한 만큼 시간을 확보하도록 끝에서 쉽니다.
SECONDS_PER_CALL = PACING["seconds_per_call"]

#  마지막으로 자동정리(cleanup)를 돈 날짜(태평양 기준). 하루 1번만 돌게.
_last_cleanup_day = None


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


# =============================================================
#  (제거) maybe_reprocess ― 배치 재처리 폐지.
#   사유: 3점대 코드를 보완해도 모델 한계가 천장이라 실효가 없었고,
#   3.5 Flash(유료)에 의존해 무료 운영 원칙과 맞지 않았습니다.
#   대신 orchestrator 의 자가발전 루프(실패에서 교훈 추출)로 대체했습니다.
# =============================================================


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

            # (1) 한도 안전선(보조 방어선)에 닿았으면 잠시 쉬고 다시 확인.
            #     평소엔 아래 유동 페이싱이 한도 아래로 묶어주므로 여기 거의 안 걸림.
            #     혹시 모를 어긋남에 대비한 이중 안전장치입니다.
            if over_limit():
                console.print(
                    "[bold yellow]⏸  오늘 한도 도달 — 리셋(태평양 자정)까지 대기[/bold yellow]"
                )
                time.sleep(600)   # 10분 쉬고 다시 확인
                continue

            # (2) 작업 시작 전: 시각과 현재 호출 수를 기록해 둡니다.
            #     작업이 끝난 뒤 "이번 작업이 호출을 몇 번 했나 / 시간이 얼마 걸렸나"를
            #     계산해, 호출 수에 비례한 대기를 주기 위함입니다(유동 페이싱).
            t_start = time.time()
            u0 = get_usage()
            calls0 = max(u0["26b"]["calls"], u0["31b"]["calls"])

            # (3) 주제 생성 → 6단계 실행 → 저장
            topic_info = topic_gen.generate()
            result = agent.run(topic_info["topic"])
            result_id = db.save(topic_info["topic"], result)

            # 저장됐는지(4점 이상) 결과 표시
            log_task_result(saved=result_id is not None, result_id=result_id)

            # (4) 유동 페이싱: 이번 작업이 쓴 호출 수만큼 시간을 확보합니다.
            #     목표시간 = 쓴 호출 수 × SECONDS_PER_CALL
            #     대기     = 목표시간 − 이미 걸린 시간  (음수면 0 = 안 쉼)
            #     이렇게 하면 하루 호출 수가 자연히 (86400 / SECONDS_PER_CALL) 이하로 갇힘.
            u1 = get_usage()
            calls1 = max(u1["26b"]["calls"], u1["31b"]["calls"])
            used = max(0, calls1 - calls0)          # 이번 작업이 쓴 호출 수
            elapsed = time.time() - t_start          # 이번 작업에 실제 걸린 시간
            target = used * SECONDS_PER_CALL          # 확보해야 할 목표 시간
            wait = max(0, target - elapsed)
            if wait > 0:
                time.sleep(wait)

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
