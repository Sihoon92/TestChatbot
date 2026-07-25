"""xlwings 로 엑셀을 읽기 전용으로 여는 얇은 래퍼.

openpyxl 을 못 쓰는 환경을 위해 실제 Excel(COM) 을 구동한다. App 을 숨김으로
띄우고, 끝나면 반드시 닫아 유령 EXCEL.EXE 프로세스가 남지 않게 한다.
값 읽기만 제공한다(수정/저장 없음).

Note on `.options(ndim=2)` (applies to every read below — `range_values`,
`used_values`, `column_values`): xlwings 는 1행/1열 범위를 평면(1D) 리스트로,
단일 셀은 스칼라로 돌려줘 값 모양만으로는 행/열 방향을 구분할 수 없다.
`ndim=2` 를 강제하면 실제 범위 크기 기준으로 항상 올바른 모양의 2D 리스트를
얻는다(빈 범위/단일 셀 포함).

## 스레드와 COM (중요 — 이 모듈 전체가 이 제약 위에서 설계됨)

Windows COM 은 기본적으로 STA(단일 스레드 아파트먼트)로 동작한다. 어떤 스레드가
COM 객체를 쓰려면 그 스레드에서 먼저 `CoInitialize()` 가 불려야 하고, 더 중요한
제약으로 **한 스레드에서 만든 COM 포인터(App/Book/Sheet/Range)는 그 스레드가
아닌 다른 스레드에서 직접 호출할 수 없다** — 그 다른 스레드에서 별도로
`CoInitialize()` 를 호출해도 마찬가지다(실측: CoInitialize 안 하면
-2147221008 'CoInitialize가 호출되지 않았습니다', 다른 스레드에서
CoInitialize 만 하고 그대로 쓰면 -2147417842 'RPC_E_WRONG_THREAD' 로 바뀔 뿐
여전히 실패한다).

`open_workbook` 의 호출자(LangGraph 에이전트)는 워크북을 한 번 연 뒤 그
`Workbook` 을 여러 도구에 바인딩해두고, 그 도구들을 LangGraph 가 나중에
(동기 `invoke()` 안에서도) 별도의 ThreadPoolExecutor 워커 스레드에서 실행한다
— 즉 open 한 스레드와 값을 읽는 스레드가 다른 게 정상 경로다. 그래서 이
모듈은 "호출한 스레드에서 COM 초기화"가 아니라, **모든 COM 객체의 생성·조회·
정리를 이 컨텍스트 전용의 스레드 하나(`_executor`, max_workers=1)에 고정**하는
방식을 쓴다. `open_workbook`/`Workbook` 의 각 메서드는 그 전용 스레드로 작업을
위임하고 결과만 돌려받으므로, 호출자는 자신이 어느 스레드에 있든(메인 스레드,
LangGraph 워커 스레드 등) 신경 쓸 필요가 없다. `CoInitialize()`/`CoUninitialize()`
는 그 전용 스레드에서 한 번씩만, 반드시 짝을 맞춰 호출한다(이 스레드는
`open_workbook` 이 매번 새로 만들므로 재사용으로 인한 중복 초기화 걱정이 없다).
"""
import concurrent.futures
import gc
import traceback
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

import pythoncom
import xlwings as xw

from app.excel.grid import col_to_letter

_T = TypeVar("_T")


def _render_exception(exc: BaseException) -> str:
    """예외를 COM 객체 참조 없이 문자열로만 렌더링한다.

    `"".join(traceback.format_exception(exc))` 는 문자열만 돌려주므로, 원본
    예외 객체·프레임·그 프레임에 걸린 xlwings COM 프록시가 호출자 스레드로
    전혀 넘어가지 않는다(문자열은 스레드/아파트먼트 경계를 넘어도 안전하다).
    `f"{type(exc).__name__}: {exc}"` 와 달리 실패가 일어난 정확한 위치(어느
    xlwings 프레임의 몇 번째 줄)까지 보존해 진단에 쓸 수 있다.
    """
    return "".join(traceback.format_exception(exc))


def _innermost_frame_location(exc: BaseException) -> str | None:
    """예외 traceback 의 가장 안쪽(마지막) 프레임 위치를 "file:line" 문자열로 돌려준다.

    `Workbook._run` 이 LLM 프롬프트에 실리는 짧은 메시지에 최소한의 진단
    실마리를 덧붙이기 위해 쓴다. 프레임/COM 객체 자체는 붙잡지 않고
    파일명·줄번호만 문자열로 뽑아내므로 `_render_exception` 과 같은 이유로
    스레드/아파트먼트 경계를 넘어도 안전하다.
    """
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return None
    return f"{last.tb_frame.f_code.co_filename}:{last.tb_lineno}"


def _com_thread_init() -> None:
    """전용 워커 스레드가 시작될 때 그 스레드에 COM(STA)을 초기화한다."""
    pythoncom.CoInitialize()


def _com_thread_uninit() -> None:
    """`_com_thread_init` 과 반드시 짝을 맞춰, 같은 스레드가 끝나기 전에 호출한다."""
    pythoncom.CoUninitialize()


class WorkbookClosedError(RuntimeError):
    """`open_workbook` 의 with 블록을 벗어난 뒤 `Workbook` 메서드를 호출하면 발생한다.

    예: LangGraph 에이전트 실행이 `with open_workbook(...) as wb:` 블록보다
    오래 살아남아, 블록이 끝난 뒤에도 도구가 `wb` 를 계속 참조하며 호출하는 경우.
    """


class WorkbookCleanupError(RuntimeError):
    """정리(cleanup) 단계(`book.close()`/`app.quit()`)에서 COM 호출이 실패했을 때 발생한다.

    중요: 이 예외는 항상 **새로 만들어** 던진다 — 원본 예외 객체를 그대로
    다시 던지거나(`raise`) `raise ... from exc` 로 체이닝하지 않는다. 원본
    예외 객체의 `__traceback__` 은 그 예외가 지나온 모든 프레임(예:
    xlwings `Book.close`/`App.quit` 내부의 `self`)의 지역변수를 살려두는데,
    `concurrent.futures.Future.__get_result` 는 워커 스레드에서 던져진
    예외 객체를 __traceback__ 째로 그대로 호출자 스레드에 재던지기 때문에,
    원본을 그대로 던지면 그 프레임에 걸린 xlwings COM 프록시(App/Book)가
    호출자 스레드로 "탈출"한다 — 이 프록시의 마지막 릴리스가 호출자
    스레드(이 전용 워커가 join 되고 CoUninitialize 된 뒤)에서 실행되면
    이 모듈이 곳곳에서 막고 있는 바로 그 크로스 아파트먼트 릴리스가
    재현된다. 그래서 여기서는 원본 예외를 문자열로만 렌더링해 메시지에
    담고, `except` 블록 **밖**에서 완전히 새 예외를 던져 원본 객체·
    프레임·(암묵적 `__context__` 체이닝까지) 전부 이 스레드 안에서 끝낸다.
    """


class WorkbookOperationError(RuntimeError):
    """`Workbook` 의 조회 메서드(`sheet_names`/`used_shape`/`range_values`/
    `used_values`/`column_values`)가 전용 워커 스레드에서 실패했을 때 발생한다.

    예: 존재하지 않는 시트 이름, 잘못된 셀 주소. `_run` 이 위임한 워커 스레드
    안에서 `self._book.sheets[sheet]`/`sht.range(...)` 등이 던지는 예외는
    xlwings 라이브러리 내부 프레임(`Sheets.__getitem__`, `Range` 생성자 등)에
    `self` 로 살아있는 `xw.Sheets`/`xw.Sheet`/`xw.Range` COM 프록시를 물고 있다.
    `WorkbookCleanupError` 와 같은 이유로 원본 예외 객체를 그대로 재던지지
    않는다 — `concurrent.futures.Future.__get_result` 가 그 프레임들을
    __traceback__ 째 호출자 스레드로 그대로 넘기면, 그 프록시들의 마지막
    릴리스가 호출자 스레드(전용 워커가 아직 살아있더라도 그 소유 스레드가
    아닌 곳)에서 실행되는 크로스 아파트먼트 릴리스가 재현되기 때문이다.

    **caught-and-raised 는 반드시 워커 스레드 쪽에서**: `_run` 은 캐치·렌더·
    재던지기를 전부 `_worker` 라는 워커 스레드 쪽 클로저 안에서 수행한다
    (`_open_book`/`_make_app`/`_cleanup`/`_quit` 과 동일한 패턴). 호출자
    스레드의 `_run` 은 `future.result()` 가 돌려주는, 이미 안전하게 완성된
    이 예외를 그대로 재던지기만 한다 — 만약 캐치를 호출자 스레드에서
    한다면 `future.result()` 시점에 이미 원본 예외가 __traceback__ 째
    호출자 스레드로 넘어온 뒤이므로 늦다.

    이 예외의 메시지는 다섯 조회 메서드 중 유일하게 `create_react_agent` 의
    `ToolNode` 를 거쳐 LLM 프롬프트에 그대로 들어간다(`handle_tool_errors=True`
    기본값이 예외를 `repr(e)` 로 감싼다) — 다른 네 지점은 `open_workbook`
    자체를 벗어나 오퍼레이터에게만 보이므로 전체 traceback 렌더를 유지하지만,
    여기는 에이전트 재시도마다 반복되므로 메시지를 짧게(대략 250자) 자른다
    (`f"{type(exc).__name__}: {exc}"` + 가능하면 가장 안쪽 프레임의
    `file:line`). 전체 렌더(`_render_exception`)는 `exc.add_note(...)` 로만
    붙인다 — `ToolNode` 가 담는 `repr(e)` 에는 note 가 포함되지 않으므로
    프롬프트는 짧고, 예외 객체를 직접 보는 오퍼레이터(로그 등)는 여전히
    전체 정보를 얻는다.
    """


class Workbook:
    """xlwings 값 조회 래퍼.

    모든 실제 xlwings/COM 호출은 `_executor`(전용 워커 스레드 하나)로 위임된다
    — 모듈 docstring의 "스레드와 COM" 절 참고. 호출자는 어느 스레드에서
    이 메서드들을 부르든 신경 쓸 필요가 없다.
    """

    def __init__(self, executor: concurrent.futures.ThreadPoolExecutor, book: "xw.Book") -> None:
        self._executor = executor
        self._book = book

    def _run(self, fn: Callable[[], _T]) -> _T:
        """COM 을 만지는 콜러블을 전용 워커 스레드에서 실행하고 결과를 돌려준다.

        캐치·렌더링·재던지기를 전부 워커 스레드 쪽 클로저(`_worker`, 아래)
        안에서 수행한다 — `_open_book`/`_make_app`/`_cleanup`/`_quit` 과
        동일한, 이 모듈에서 이미 검증된 패턴이다(`WorkbookOperationError`
        문서 참고). `fn` 이 던진 원본 예외는 워커 스레드에서 캐치해 문자열로만
        렌더링하고, `except` 블록 **밖**에서 완전히 새 `WorkbookOperationError`
        를 던진다 — 원본 객체·프레임(그 안의 xlwings COM 프록시)·암묵적
        `__context__` 체이닝이 전부 워커 스레드 안에서 끝난다. 캐치를 이
        (호출자) 스레드에서 했다면 이미 늦다: `future.result()` 가 원본
        예외를 __traceback__ 째 호출자 스레드로 넘긴 **뒤에야** 캐치하게
        되므로, 그 프레임에 걸린 COM 프록시가 호출자 스레드에서 파이널라이즈
        되는 크로스 아파트먼트 릴리스를 막지 못한다. 그래서 아래 `_run` 본문은
        `future.result()` 가 돌려주는(또는 재던지는) 값/예외를 그대로
        전달하기만 한다 — 다시 감싸지 않는다.

        의도적으로 남겨둔 좁은 한계: `_worker` 는 `Exception` 만 잡는다.
        `KeyboardInterrupt`/`GeneratorExit` 같은 `BaseException` 이 워커 실행
        도중 발생하는 극히 드문 경우, 원본 프레임(및 그 안의 COM 프록시)이
        그대로 넘어갈 수 있다 — catch 범위를 넓히지 않기로 결정했다.
        """
        if self._book is None:
            # open_workbook 의 with 블록이 이미 종료되어 워커 스레드/Excel 프로세스가
            # 정리된 뒤다. executor 는 shutdown 되어 있어 그냥 submit 하면
            # "cannot schedule new futures after shutdown" 라는 내부 구현 디테일이
            # 그대로 새어나간다 — 여기서 먼저 걸러 의도가 분명한 도메인 에러로 바꾼다.
            raise WorkbookClosedError(
                "open_workbook 컨텍스트가 이미 종료되어 이 Workbook 은 더 이상 사용할 수 없다."
            )

        def _worker() -> _T:
            # 전용 워커 스레드에서 실행된다(executor.submit) — COM 객체의
            # 소유 스레드에서 캐치·렌더·재던지기를 끝내야 하는 이유는 위
            # `_run` docstring 참고. `fn()` 이 성공하면 `op_error_short` 가
            # None 으로 남아 아래 `if` 를 건너뛰고 바로 `result` 를 돌려준다
            # (`_open_book` 의 `open_error`/`book` 패턴과 동일).
            result: _T | None = None
            op_error_short: str | None = None
            op_error_full: str | None = None
            try:
                result = fn()
            except Exception as exc:  # noqa: BLE001
                op_error_short = f"{type(exc).__name__}: {exc}"
                location = _innermost_frame_location(exc)
                if location is not None:
                    op_error_short = f"{op_error_short} ({location})"
                op_error_full = _render_exception(exc)
            if op_error_short is not None:
                # LLM 프롬프트에 실리는 메시지는 짧게 자른다(도구 반환을
                # 항상 잘라서 주는 이 레이어의 다른 관례와 동일 — 컨텍스트
                # 보호). 전체 렌더는 note 로만 붙여 오퍼레이터/로그에서는
                # 여전히 전체 정보를 볼 수 있게 한다 — `WorkbookOperationError`
                # 문서 참고.
                if len(op_error_short) > 250:
                    op_error_short = op_error_short[:250] + "…(truncated)"
                err = WorkbookOperationError(f"엑셀 작업 실패: {op_error_short}")
                err.add_note(op_error_full)
                raise err
            return result

        # 위 self._book is None 검사와 아래 submit() 사이에는 시간 간격이 있다
        # (TOCTOU) — 이 간격에 (예: LangGraph 가 도구를 별도 스레드에서 실행하는
        # 동안) `open_workbook` 의 with 블록이 다른 스레드에서 동시에 종료되면
        # executor 가 그 사이에 shutdown 되어, 검사는 통과했지만 submit() 에서
        # 여전히 RuntimeError 가 새어나갈 수 있다. `ThreadPoolExecutor.submit`
        # 은 이 경우 파이썬 버전에 따라 메시지가 다른 RuntimeError 를 던진다
        # ("cannot schedule new futures after shutdown" 대
        # "... after interpreter shutdown") — 문자열 매칭 대신 상태를
        # 재확인한다. `_cleanup` 이 `wb._book = None` 을 executor shutdown 보다
        # 반드시 먼저(같은 워커 스레드에서 순서대로) 실행하므로, 이 submit()
        # 이 RuntimeError 로 실패한 시점에 `self._book` 이 이미 None 이라면
        # 원인은 shutdown 뿐이라고 확정할 수 있다.
        #
        # 주의: 이 재확인은 executor.submit() 자체가 (shutdown 상태라서) 던지는
        # RuntimeError 만 잡는다 — `self._book` 이 None 이 아닌 채로 with 블록이
        # 열려 있는 동안 인터프리터 종료(interpreter shutdown)가 시작되면 같은
        # 메시지의 RuntimeError 가 이 submit() 에서 새어나갈 수 있는데, 그때는
        # `self._book is None` 이 False 이므로 아래 `raise` (원본 그대로)로
        # 빠진다 — 즉 "state 재확인은 `_book is None` 인 경우만 도메인 에러로
        # 바꾼다"는 좁은 커버리지다.
        try:
            future = self._executor.submit(_worker)
        except RuntimeError:
            if self._book is None:
                raise WorkbookClosedError(
                    "open_workbook 컨텍스트가 이미 종료되어 이 Workbook 은 더 이상 사용할 수 없다."
                ) from None
            raise
        # `_worker` 가 이미 워커 스레드에서 안전하게 렌더링/재던지기를 끝냈다
        # (성공하면 값을, 실패하면 완성된 WorkbookOperationError 를 갖고 있다).
        # 여기서는 그 결과를 그대로 돌려주거나 재던지기만 한다 — 다시 감싸지
        # 않는다.
        return future.result()

    def sheet_names(self) -> list[str]:
        return self._run(lambda: [s.name for s in self._book.sheets])

    def _sheet(self, sheet: str) -> "xw.Sheet":
        # 주의: COM 프록시(self._book)를 직접 만지므로 반드시 `_run` 이 위임한
        # 전용 워커 스레드 안에서만 호출해야 한다. 호출자 스레드에서 이 메서드를
        # 직접 부르면 RPC_E_WRONG_THREAD 로 실패한다 — 모듈 docstring의
        # "스레드와 COM" 절 참고.
        return self._book.sheets[sheet]

    def used_shape(self, sheet: str) -> tuple[int, int]:
        def _do() -> tuple[int, int]:
            rng = self._sheet(sheet).used_range
            return (rng.rows.count, rng.columns.count)

        return self._run(_do)

    def range_values(self, sheet: str, address: str) -> list[list]:
        def _do() -> list[list]:
            # ndim=2 rationale: see module docstring.
            return self._sheet(sheet).range(address).options(ndim=2).value

        return self._run(_do)

    def used_values(self, sheet: str) -> tuple[list[list], str]:
        def _do() -> tuple[list[list], str]:
            rng = self._sheet(sheet).used_range
            top_left = rng[0, 0].get_address(False, False)  # 예: "A1"
            return rng.options(ndim=2).value, top_left

        return self._run(_do)

    def column_values(self, sheet: str, column: str, max_rows: int = 5000) -> list:
        def _do() -> list:
            # 주의: used_range 는 A1 이 아닌 곳(예 C3)에서 시작할 수 있다. 예전
            # 버전은 `rows.count`(행 개수)를 행 인덱스처럼 써서 `{column}1:
            # {column}{nrows}` 를 읽었는데, 이는 used_range 가 A1 부터 시작할
            # 때만 우연히 맞다 — offset 이 있으면 위쪽에 엉뚱한 빈 행을 포함하고
            # 아래쪽 실제 데이터 행은 잘려나간다(used_values 는 이미
            # rng[0, 0].get_address 로 offset-aware 하게 top_left 를 구하므로
            # 이 메서드만 어긋나 있었다). used_range 자신의 주소(.row/.rows.count)
            # 에서 첫 행·끝 행을 직접 구해 그 구간만 읽는다.
            used = self._sheet(sheet).used_range
            first_row = used.row
            last_row = used.row + used.rows.count - 1
            end_row = min(last_row, first_row + max_rows - 1)
            rng = self._sheet(sheet).range(f"{column}{first_row}:{column}{end_row}")
            return [row[0] for row in rng.options(ndim=2).value]

        return self._run(_do)


@contextmanager
def open_workbook(path: str) -> Iterator[Workbook]:
    # 이 컨텍스트 전용의 워커 스레드 하나에 App/Book COM 객체의 생성부터 정리까지
    # 전부 고정한다 — 이유는 모듈 docstring "스레드와 COM" 절 참고. `open_workbook`
    # 을 호출한 스레드(메인 스레드일 수도, 다른 워커 스레드일 수도 있다)는 이
    # 함수 본문(제너레이터 이전/이후 코드)만 그대로 실행하고, 실제 COM 호출은
    # 전부 `executor.submit(...).result()` 로 이 전용 스레드에 위임한다.
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="xlwings-com"
    )
    # CoInitialize 가 실제로 성공했을 때만 짝이 되는 CoUninitialize 를 부르기
    # 위한 플래그. `.result()` 가 예외를 던지면(초기화 실패) 이 값이 False로
    # 남아, 초기화된 적 없는 스레드에 CoUninitialize 를 잘못 호출하는 일을 막는다.
    com_initialized = False
    try:
        executor.submit(_com_thread_init).result()
        com_initialized = True

        def _make_app() -> "xw.App":
            app = xw.App(visible=False, add_book=False)
            # xw.App(...) 시점에 이미 EXCEL.EXE 프로세스가 떠 있다. 아래 속성
            # 대입 중 하나라도 실패하면 그 예외만 퓨처를 통해 전파되고 지역
            # 변수 app 은 버려지는데, 그러면 아무도 quit() 을 부르지 못해
            # "유령 프로세스 방지" 제약을 어기는 EXCEL.EXE 가 남는다. 그래서
            # 실패 시 여기서 먼저 quit() 하고 나서 원래 예외를 다시 던진다.
            #
            # 단, 원본 예외 객체를 그대로 다시 던지면(`raise`) 그 예외의
            # __traceback__ 프레임에 걸린 이 지역변수 app(xw.App COM 프록시)이
            # Future.__get_result 를 통해 호출자 스레드까지 그대로 실려간다
            # (WorkbookCleanupError 문서 참고 — 이 함수도 같은 위험에 노출돼
            # 있다). 그래서 여기서도 원본을 문자열로만 렌더링해 담고, quit() 을
            # 방어적으로(그 자체가 실패해도 삼켜서 원래 원인을 가리지 않게)
            # 호출한 뒤, 지역변수를 끊고 나서 완전히 새 예외를 던진다.
            setup_error: str | None = None
            try:
                app.display_alerts = False
                app.screen_updating = False
            except Exception as exc:  # noqa: BLE001
                setup_error = _render_exception(exc)
            if setup_error is not None:
                # quit() 자체가 실패해도 진짜 원인(setup_error)을 가리지 않도록
                # non-demoting 하게(예외 타입/우선순위를 바꾸지 않고) 처리한다 —
                # 다만 이전에는 이 실패를 조용히 삼켜서, 진짜로 유령 프로세스가
                # 남는 경우에도 아무 증거가 남지 않았다(Minor 3). 여기서는
                # quit_error 로 캡처해 최종 메시지에 덧붙여 가시성을 남긴다.
                quit_error: str | None = None
                try:
                    app.quit()
                except Exception as exc:  # noqa: BLE001
                    quit_error = _render_exception(exc)
                app = None
                if quit_error is not None:
                    raise RuntimeError(
                        f"Excel App 초기화 실패: {setup_error} "
                        f"(정리용 quit() 도 실패: {quit_error})"
                    )
                raise RuntimeError(f"Excel App 초기화 실패: {setup_error}")
            return app

        app = executor.submit(_make_app).result()
        try:
            def _open_book() -> "xw.Book":
                # app.books.open() 이 실패하는 입력(존재하지 않는 경로, 잠긴
                # 파일, 손상된 워크북 등)은 LLM 에이전트가 만들어낼 가능성이
                # 가장 높은 나쁜 입력이다. 실패하면 그 예외는 xlwings 내부
                # 프레임(예: Books.open 내부의 self=xw.Books/xw.App)에 걸린
                # 채로 전파된다 — _make_app 과 동일한 크로스 아파트먼트 위험
                # (WorkbookCleanupError 문서 참고). 그래서 여기서도 원본을
                # 그대로 다시 던지지 않고, 렌더링한 문자열만 담아 새 예외를
                # 던진다.
                open_error: str | None = None
                try:
                    book = app.books.open(path, read_only=True, update_links=False)
                except Exception as exc:  # noqa: BLE001
                    open_error = _render_exception(exc)
                if open_error is not None:
                    raise RuntimeError(f"워크북 열기 실패: {open_error}")
                return book

            book = executor.submit(_open_book).result()
            wb = Workbook(executor, book)
            try:
                yield wb
            finally:
                def _cleanup() -> None:
                    # book.close() 만으로는 COM 프록시가 즉시 해제되지 않는다:
                    # 호출자가 `with open_workbook(...) as wb:` 블록을 벗어난
                    # 뒤에도 `wb`(Workbook 래퍼) 변수를 계속 들고 있을 수 있고,
                    # 그 래퍼의 `_book` 속성이 여전히 이 xw.Book COM 프록시를
                    # 강하게 참조한다. 그래서 여기서 래퍼의 내부 참조
                    # (`wb._book`)와 `open_workbook` 프레임의 `book` 을 모두
                    # (nonlocal 로) 끊고, Excel 프로세스가 아직 살아있는 이
                    # 시점에(그리고 COM 프록시의 소유 스레드인 여기서)
                    # gc.collect() 로 즉시 파이널라이즈되도록 유도한다(COM
                    # 프록시 정리는 앱 종료 전에 하는 것이 원칙).
                    #
                    # 이 참조 끊기(및 gc.collect())를 nonlocal/wb._book 을 통해
                    # **반드시** 실행하는 이유: 안 그러면 `open_workbook` 의
                    # 제너레이터 프레임이나 `wb` 래퍼가 COM 프록시를 계속 들고
                    # 있다가, 이 전용 스레드가 join 되고 CoUninitialize 된 뒤
                    # **호출자 스레드**에서 마지막 참조가 풀려 파이널라이저가
                    # 그 스레드(COM 미초기화 상태)에서 실행된다 — 실제로 이 정리를
                    # 빼먹었을 때 0x800401F0(-2147221008, CO_E_NOTINITIALIZED)가
                    # 호출자 스레드에서 재현됐다.
                    #
                    # book.close() 자체가 예외를 던질 수도 있다(Excel 모달 상태,
                    # RPC 실패, 읽기 전용 잠금 등 — 이 모듈이 이미 겪고 있는
                    # RPC_S_SERVER_UNAVAILABLE 노이즈도 바로 이 경로에서 난다).
                    # close() 가 실패하더라도 위와 같은 이유로 참조 끊기와
                    # gc.collect() 는 반드시 실행돼야 하므로 try/finally 로 묶는다.
                    # app.quit() 은 바깥 finally 에서 별도로 보장되므로 여기서
                    # close() 실패를 그대로 다시 던져도 프로세스가 남지 않는다 —
                    # 다만 "그대로"(원본 예외 객체를 `raise` 로 재던짐)는 하지
                    # 않는다: WorkbookCleanupError 문서에 적었듯, 원본 예외의
                    # __traceback__ 프레임(xlwings Book.close 내부의 self 등)이
                    # Future 를 통해 호출자 스레드까지 살아서 넘어가 그 프록시의
                    # 마지막 릴리스가 호출자 스레드에서 실행되는 걸 막아야 한다.
                    # 그래서 원본은 문자열로만 렌더링해 담고(close_error),
                    # try/finally/if 블록이 전부 끝난 뒤(= except 블록 밖, 진행
                    # 중인 예외 없음) 완전히 새 예외를 던진다 — 원본 객체·
                    # 프레임·암묵적 __context__ 체이닝 전부 여기서 끝난다.
                    nonlocal book
                    close_error: str | None = None
                    try:
                        book.close()
                    except Exception as exc:  # noqa: BLE001
                        close_error = _render_exception(exc)
                    finally:
                        wb._book = None
                        book = None
                        # 실측치(0x800706ba 관련 배경 — 자세한 현상은 모듈
                        # 하단 CoUninitialize 근처 주석 참고): 이 gc.collect() 를
                        # 추가하기 전에는 `Windows fatal exception: code
                        # 0x800706ba` 가 테스트 당 3회 발생했고, 추가한 뒤로는
                        # 테스트 당 2회로 줄었다. 근본 원인 규명은 못 했지만,
                        # 이 호출이 현상의 빈도에 실제 영향을 준다는 유일한
                        # 근거라서 유지한다.
                        gc.collect()
                    if close_error is not None:
                        raise WorkbookCleanupError(f"book.close() 실패: {close_error}")

                executor.submit(_cleanup).result()
        finally:
            def _quit() -> None:
                # _cleanup 의 `book` 과 같은 이유로, `app` 도 nonlocal 로 끊어서
                # open_workbook 프레임 정리 시 호출자 스레드에서 COM 파이널라이저가
                # 도는 일을 막는다. app.quit() 이 예외를 던져도(RPC 실패 등)
                # `app = None` 은 반드시 실행돼야 한다 — 안 그러면 이 프레임의
                # `app` 지역변수가 마지막 참조로 남아 호출자 스레드에서
                # 파이널라이즈되며 같은 부류의 크래시를 일으킨다.
                #
                # _cleanup 과 같은 이유로 원본 예외 객체는 그대로 던지지 않는다
                # (WorkbookCleanupError 문서 참고) — 문자열로만 렌더링해 담고,
                # try/finally/if 가 전부 끝난 뒤 새 예외를 던진다.
                nonlocal app
                quit_error: str | None = None
                try:
                    app.quit()
                except Exception as exc:  # noqa: BLE001
                    quit_error = _render_exception(exc)
                finally:
                    app = None
                if quit_error is not None:
                    raise WorkbookCleanupError(f"app.quit() 실패: {quit_error}")

            executor.submit(_quit).result()
    finally:
        # CoUninitialize 는 CoInitialize 를 호출한 바로 그 스레드에서, 그
        # 스레드가 끝나기 전 마지막으로 실행돼야 한다(요구사항 #2). 이 실행기는
        # 이 with 블록 전용이라 이후 재사용되지 않으므로, 마지막 작업으로
        # 큐에 넣고 나서 스레드를 종료(shutdown)한다.
        #
        # 실측 결과(참고용, 수정 전부터 있던 기존 한계 — 새로 억제하지 않음):
        # 전용 워커 스레드 도입 후에도 `pytest`(플래그 없이, faulthandler 기본
        # 활성) 실행 시 `Windows fatal exception: code 0x800706ba`
        # (RPC_S_SERVER_UNAVAILABLE) 가 여전히 테스트 당 2회, 각 테스트의 PASSED
        # 직전에(정리 경로 근처) 출력된다 — 도입 전과 빈도·시점이 동일하다.
        # faulthandler 크래시 덤프에는 대부분 스레드 섹션이 하나만 찍혀 그것이
        # 이 파일의 전용 워커 스레드인지 호출자 스레드인지 스택만으로는 완전히
        # 단정하지 못했다(어느 쪽이든 실행 후 EXCEL.EXE 잔여 프로세스는 없고
        # 작업은 정상 완료됨은 확인함). 근본 원인은 여전히 미확정이며, 러너
        # 플래그나 설정으로 억제하지 않고 있는 그대로 둔다.
        try:
            if com_initialized:
                executor.submit(_com_thread_uninit).result()
        finally:
            executor.shutdown(wait=True)


__all__ = [
    "open_workbook",
    "Workbook",
    "WorkbookClosedError",
    "WorkbookCleanupError",
    "WorkbookOperationError",
    "col_to_letter",
]
