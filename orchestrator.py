# =============================================================
#  orchestrator.py  ―  멀티에이전트 루프 (이 시스템의 심장)
# -------------------------------------------------------------
#  하는 일:
#   하나의 주제를 받아서, 아래 6단계를 거쳐 코드를 완성합니다.
#
#   1. 지시   (26B) : 작업 계획 세우기
#   2. 코딩   (31B) : 코드 작성 (주석 꼼꼼히!)
#   3. AST검사       : 위험하면 → 2번으로 (코딩 직후 즉시 차단)
#   4. 디버깅 (26B) : 로직 점검, 문제 있으면 → 2번으로
#   5. 검토   (31B) : 최종 점검
#   6. 평가   (31B) : 1~5점 채점
#
#   역할 분담(3:3 균형):
#     26B = 지시 · 디버깅      (+ 주제생성은 topic_generator 에서)
#     31B = 코딩 · 검토 · 평가
#   → 두 모델 사용량이 비슷해져서 한도 병목을 피합니다.
# =============================================================

import os
import re
import json
from datetime import datetime

from notebook import MistakeNotebook
from agents import call_gemma_26b, call_gemma_31b, call_flash
from safety import CodeSafetyChecker
from logger import (
    log_station, log_ast_result, log_evaluation, log_task_start,
)
from config import MAX_DEBUG_ITERATIONS, LOG_PATH


class CodeAgent:
    """주제 하나를 받아 6단계로 코드를 완성하는 일꾼."""

    def __init__(self):
        self.notebook = MistakeNotebook()       # 오답노트
        self.safety = CodeSafetyChecker()        # AST 검사기
        self.task_count = 0                      # 지금까지 처리한 작업 수

    # -------------------------------------------------------------
    #  로그를 파일에도 남깁니다(화면 로그와 별개로 기록 보관용).
    # -------------------------------------------------------------
    def log_file(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs("logs", exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    # -------------------------------------------------------------
    #  AI 응답에서 코드 블록(```python ... ```)만 뽑아냅니다.
    #  설명 글이 섞여 와도 순수 코드만 추출합니다.
    # -------------------------------------------------------------
    def _extract_code(self, text):
        match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    # =============================================================
    #  1단계: 지시 (26B) ― 작업 계획 세우기
    # =============================================================
    def step_1_instruct(self, idea, mistakes_context):
        log_station("지시실", "계획 수립 중...")
        prompt = f"""당신은 코딩 작업 계획자입니다.

# 사용자 요청
{idea}

# 과거 실수 (참고)
{mistakes_context or "이전 실수 없음"}

# 작업
위 요청을 구현하기 위한 단계별 계획을 작성하세요.

# 출력 형식
1. 구현 목표:
2. 기술 스택:
3. 단계별 계획:
4. 주의사항:
"""
        plan = call_gemma_26b(prompt)
        log_station("지시실", "계획 수립 완료")
        return plan

    # =============================================================
    #  2단계: 코딩 (31B) ― 코드 작성 (주석 꼼꼼히!)
    # =============================================================
    def step_2_code(self, plan, current_code, mistakes_context, iteration):
        log_station("코딩실", f"코드 생성 중... (반복 {iteration})")

        # 처음이면 새로 작성, 아니면 기존 코드 수정.
        task_line = (
            "완전한 Python 코드를 작성하세요."
            if iteration == 1
            else "기존 코드의 문제를 수정하세요."
        )

        prompt = f"""당신은 전문 개발자입니다.

# 구현 계획
{plan}

# 현재 코드
{current_code or "(처음 시작)"}

# 과거 실수 (참고)
{mistakes_context or "이전 실수 없음"}

# 작업
{task_line}

# 매우 중요한 규칙
- 모든 함수와 주요 로직에 한국어 주석을 꼼꼼하게 달아주세요.
- 프로그래밍 초보자도 읽고 이해할 수 있도록 설명하세요.
- 각 부분이 "무엇을, 왜" 하는지 주석으로 남기세요.

# 출력
완성된 Python 코드만 출력하세요 (코드 외 설명은 쓰지 마세요).
"""
        code = call_gemma_31b(prompt)
        code = self._extract_code(code)
        log_station("코딩실", f"코드 생성 완료 ({len(code)}자)")
        return code

    # =============================================================
    #  3단계: AST 안전 검사 ― 코딩 직후 즉시!
    #   위험하면 디버깅까지 안 가고 바로 코딩 단계로 되돌립니다.
    #   (낭비를 줄이는 "빠른 실패" 구조)
    # =============================================================
    def step_3_ast_check(self, code):
        is_safe, msg = self.safety.check(code)
        log_ast_result(is_safe, msg)
        return is_safe, msg

    # =============================================================
    #  4단계: 디버깅 (26B) ― 로직 점검
    #   안전은 이미 통과한 코드이므로, 여기선 로직만 봅니다.
    # =============================================================
    def step_4_debug(self, code, mistakes_context):
        log_station("연구소", "로직 분석 중...")
        prompt = f"""당신은 코드 리뷰어입니다.

# 코드
{code}

# 과거 실수 패턴 (참고)
{mistakes_context or "이전 실수 없음"}

# 작업 (안전은 이미 통과한 코드입니다. 로직만 보세요)
1. 구문 오류
2. 논리 오류
3. 과거 실수 패턴 재현 여부
4. 성능 문제

# 출력 형식
문제가 없으면 첫 줄에 정확히: OK
문제가 있으면:
- 문제 1: (설명)
- 문제 2: (설명)
"""
        result = call_gemma_26b(prompt)
        log_station("연구소", "분석 완료")
        return result

    # =============================================================
    #  5단계: 최종 검토 (31B)
    # =============================================================
    def step_5_review(self, code, plan, mistakes_context):
        log_station("검토실", "최종 검토 중...")
        prompt = f"""당신은 시니어 개발자입니다.

# 원래 계획
{plan}

# 최종 코드
{code}

# 작업
최종 검토를 수행하세요.

# 출력
## 평가: (좋음/보통/나쁨)
## 강점:
- ...
## 약점:
- ...
"""
        review = call_gemma_31b(prompt)
        log_station("검토실", "최종 검토 완료")
        return review

    # =============================================================
    #  6단계: AI 자동 평가 (31B) ― 1~5점 채점
    #   결과는 JSON 으로 받습니다. (점수 + 한줄평)
    # =============================================================
    def step_6_evaluate(self, code, idea):
        log_station("평가실", "평가 중...")
        prompt = f"""당신은 까다로운 코드 리뷰어입니다. 엄격하게 평가하세요.

# 주제
{idea}

# 코드
{code}

# 평가 기준 (각 1~5점, 엄격하게)
- 5점: 매우 우수, 프로덕션 사용 가능
- 4점: 좋음
- 3점: 보통
- 2점: 미흡
- 1점: 매우 미흡

# 평가 항목
1. 실행 가능성
2. 코드 품질
3. 독창성
4. 실용성
5. 완성도

# 출력 (JSON만, 다른 텍스트 없이)
{{
    "execution": <1-5>,
    "quality": <1-5>,
    "novelty": <1-5>,
    "practicality": <1-5>,
    "completeness": <1-5>,
    "total": <평균>,
    "summary": "<한 줄 평>"
}}
"""
        response = call_gemma_31b(prompt)
        evaluation = self._parse_json(response)

        score = evaluation.get("total", 0)
        summary = evaluation.get("summary", "평가 실패")
        log_evaluation(score, summary)
        return evaluation

    # -------------------------------------------------------------
    #  AI가 준 JSON 문자열을 안전하게 파이썬 자료로 바꿉니다.
    #  중간에 설명이 섞여 와도 { ... } 부분만 뽑아 파싱하고,
    #  실패하면 기본값(0점)을 돌려줍니다.
    # -------------------------------------------------------------
    def _parse_json(self, text):
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            self.log_file(f"평가 JSON 파싱 실패: {e}")
        return {"total": 0, "summary": "평가 실패"}

    # =============================================================
    #  전체 실행 ― 위 6단계를 순서대로 돌립니다.
    # =============================================================
    def run(self, idea):
        self.task_count += 1
        log_task_start(idea, self.task_count)
        self.log_file(f"작업 시작: {idea}")

        # 시작 전: 이 주제와 비슷한 과거 실수를 찾아 둡니다.
        mistakes_context = self.notebook.format_for_prompt(idea)

        # 1. 지시 (계획 세우기)
        plan = self.step_1_instruct(idea, mistakes_context)

        # 2~4. 코딩 → AST → 디버깅 을 최대 MAX_DEBUG_ITERATIONS 번 반복
        code = ""
        completed = False
        i = 1
        for i in range(1, MAX_DEBUG_ITERATIONS + 1):
            # 2. 코딩
            code = self.step_2_code(plan, code, mistakes_context, i)

            # 3. AST 검사 (코딩 직후)
            is_safe, safety_msg = self.step_3_ast_check(code)
            if not is_safe:
                # 위험하면 디버깅 건너뛰고 바로 다시 코딩.
                mistakes_context += f"\n\n방금 문제: {safety_msg}\n안전한 코드로 작성하세요!"
                continue

            # 4. 디버깅 (로직 점검)
            debug_result = self.step_4_debug(code, mistakes_context)

            # "OK" 로 시작하면 통과 → 루프 종료
            if debug_result.strip().startswith("OK"):
                completed = True
                break
            # 아니면 문제점을 다음 코딩에 참고로 넘기고 다시.
            mistakes_context += f"\n\n방금 지적: {debug_result}"

        # 5. 최종 검토
        review = self.step_5_review(code, plan, mistakes_context)

        # 6. AI 평가
        evaluation = self.step_6_evaluate(code, idea)

        self.log_file(
            f"작업 완료: {idea} / 평점: {evaluation.get('total', 0):.1f} / "
            f"반복: {i}회 / 완성: {completed}"
        )

        # 결과를 한 묶음으로 돌려줍니다(이후 DB 저장에 사용).
        return {
            "idea": idea,
            "plan": plan,
            "code": code,
            "review": review,
            "evaluation": evaluation,
            "completed": completed,
            "iterations": i,
        }

    # =============================================================
    #  배치 재처리 ― 3점대(거의 4점) 코드 하나를 보완해 재평가.
    #   3.5 Flash 가 코드를 더 낫게 고치고 → AST 검사 → 31B 가 재채점.
    #   돌려주는 값:
    #     (새 코드, 새 점수, 새 한줄평)  또는  None(보완 실패: 위험코드 등)
    # =============================================================
    def reprocess_one(self, record):
        log_station(
            "재처리실",
            f"#{record['id']} 보완 시도 (현재 {record['score']:.1f}점)",
        )

        # 1) 3.5 Flash 에게 코드 보완을 맡깁니다.
        prompt = f"""당신은 코드 개선 전문가입니다.

# 주제
{record['topic']}

# 현재 코드 (평가 {record['score']:.1f}점 / 4점 미달)
{record['code']}

# 이전 평가 한줄평
{record['summary']}

# 작업
위 코드의 부족한 점을 보완해, 더 완성도 높은 Python 코드로 다시 쓰세요.
- 예외 처리, 엣지 케이스, 사용성을 보강하세요.
- 한국어 주석을 초보자도 이해하게 꼼꼼히 다세요.

# 출력
완성된 Python 코드만 출력하세요 (코드 외 설명 금지).
"""
        improved = call_flash(prompt)
        improved = self._extract_code(improved)

        # 2) AST 안전검사 ― 위험하면 보완 포기(원본 그대로 둠).
        is_safe, msg = self.safety.check(improved)
        log_ast_result(is_safe, msg)
        if not is_safe:
            return None

        # 3) 31B 로 재평가 (기존 평가 단계 재사용).
        evaluation = self.step_6_evaluate(improved, record["topic"])
        new_score = evaluation.get("total", 0)
        new_summary = evaluation.get("summary", "")

        return improved, new_score, new_summary
