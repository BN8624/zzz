# =============================================================
#  safety.py  ―  코드 안전성 검사 (AST 기반)
# -------------------------------------------------------------
#  하는 일:
#   AI가 생성한 코드를 "실행하지 않고" 글자만 분석해서,
#   위험한 동작(파일 삭제, 시스템 명령 실행 등)이 있는지 검사합니다.
#
#  AST(Abstract Syntax Tree)란?
#   코드를 실행하지 않고, 문법 구조만 나무(tree) 형태로 분석하는 것.
#   "이 코드 안에 rmtree() 호출이 있나?" 같은 걸 안전하게 찾아냅니다.
#   실제로 코드를 돌리지 않으므로 위험하지 않습니다.
#
#  정책(과거 결정 반영):
#   - subprocess(외부 명령 실행)는 통째로 차단
#   - shutil 모듈 자체는 허용 (파일 복사/이동은 정상 작업)
#     단, shutil 안의 위험 함수(rmtree 등 삭제류)만 차단
# =============================================================

import ast


class CodeSafetyChecker:
    """코드를 AST로 분석해 위험 요소를 찾아내는 검사기."""

    # -------------------------------------------------------------
    #  차단 대상 1: 위험한 내장 함수
    #   eval/exec/compile: 문자열을 코드로 실행 → 위험
    #   __import__: 동적으로 아무 모듈이나 불러오기 → 위험
    # -------------------------------------------------------------
    DANGEROUS_FUNCTIONS = {
        "eval", "exec", "compile", "__import__",
    }

    # -------------------------------------------------------------
    #  차단 대상 2: 위험한 모듈 (import 자체를 막음)
    #   subprocess: 외부 시스템 명령 실행(rm -rf 등) → 통째 차단
    #   (shutil 은 여기 없음 = 허용)
    # -------------------------------------------------------------
    DANGEROUS_MODULES = {
        "subprocess",
    }

    # -------------------------------------------------------------
    #  차단 대상 3: 위험한 파일 조작 함수 (이름으로 차단)
    #   삭제 계열만 막습니다. (복사/이동은 허용)
    #   예: shutil.rmtree(), os.remove(), os.unlink() ...
    # -------------------------------------------------------------
    DANGEROUS_FILE_OPS = {
        "rmtree", "remove", "unlink", "rmdir",
    }

    def check(self, code):
        """
        코드를 검사합니다.
        돌려주는 값: (안전한가?, 메시지)
          - (True,  "안전")            → 통과
          - (False, "이유 설명")        → 위험 발견 또는 문법 오류
        """
        # (1) 먼저 코드를 AST로 파싱해 봅니다.
        #     문법이 깨져 있으면 여기서 걸립니다.
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"구문 오류: {e}"

        issues = []  # 발견한 문제들을 모으는 목록

        # (2) 코드의 모든 부분(노드)을 하나씩 훑습니다.
        for node in ast.walk(tree):

            # --- 함수 호출인 경우 ---
            if isinstance(node, ast.Call):
                # 예: eval(...) 처럼 이름만으로 부르는 함수
                if isinstance(node.func, ast.Name):
                    if node.func.id in self.DANGEROUS_FUNCTIONS:
                        issues.append(f"위험 함수: {node.func.id}()")

                # 예: shutil.rmtree(...) 처럼 점(.)으로 부르는 함수
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr in self.DANGEROUS_FILE_OPS:
                        issues.append(f"위험 파일 조작: {node.func.attr}()")

            # --- import subprocess 형태 ---
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in self.DANGEROUS_MODULES:
                        issues.append(f"위험 모듈: {alias.name}")

            # --- from subprocess import ... 형태 ---
            if isinstance(node, ast.ImportFrom):
                if node.module in self.DANGEROUS_MODULES:
                    issues.append(f"위험 모듈: {node.module}")

        # (3) 문제가 하나라도 있으면 위험으로 판정
        if issues:
            return False, "; ".join(issues)

        return True, "안전"


# =============================================================
#  이 파일을 직접 실행하면(테스트용) 아래가 돌아갑니다.
#  다른 파일에서 import 할 때는 실행되지 않습니다.
# =============================================================
if __name__ == "__main__":
    checker = CodeSafetyChecker()

    # 안전한 예: 단순 덧셈
    print(checker.check("def add(a, b): return a + b"))

    # 안전한 예: shutil 로 파일 이동 (허용돼야 함)
    print(checker.check("import shutil\nshutil.move('a.txt', 'folder/')"))

    # 위험한 예: shutil.rmtree (삭제 → 차단돼야 함)
    print(checker.check("import shutil\nshutil.rmtree('/important')"))

    # 위험한 예: subprocess (외부 명령 → 차단돼야 함)
    print(checker.check("import subprocess\nsubprocess.run(['rm', '-rf', '/'])"))
