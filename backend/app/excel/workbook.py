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
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

import pythoncom
import xlwings as xw

from app.excel.grid import col_to_letter

_T = TypeVar("_T")


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
        """COM 을 만지는 콜러블을 전용 워커 스레드에서 실행하고 결과를 돌려준다."""
        if self._book is None:
            # open_workbook 의 with 블록이 이미 종료되어 워커 스레드/Excel 프로세스가
            # 정리된 뒤다. executor 는 shutdown 되어 있어 그냥 submit 하면
            # "cannot schedule new futures after shutdown" 라는 내부 구현 디테일이
            # 그대로 새어나간다 — 여기서 먼저 걸러 의도가 분명한 도메인 에러로 바꾼다.
            raise WorkbookClosedError(
                "open_workbook 컨텍스트가 이미 종료되어 이 Workbook 은 더 이상 사용할 수 없다."
            )
        # 위 self._book is None 검사와 아래 submit() 사이에는 시간 간격이 있다
        # (TOCTOU) — 이 간격에 (예: LangGraph 가 도구를 별도 스레드에서 실행하는
        # 동안) `open_workbook` 의 with 블록이 다른 스레드에서 동시에 종료되면
        # executor 가 그 사이에 shutdown 되어, 검사는 통과했지만 submit() 에서
        # 여전히 "cannot schedule new futures after shutdown" (RuntimeError) 가
        # 새어나갈 수 있다. 이 RuntimeError 를 여기서 명확한 도메인 에러로
        # 다시 변환해 그 경쟁 창을 닫는다.
        try:
            return self._executor.submit(fn).result()
        except RuntimeError as exc:
            if "cannot schedule new futures after shutdown" in str(exc):
                raise WorkbookClosedError(
                    "open_workbook 컨텍스트가 이미 종료되어 이 Workbook 은 더 이상 사용할 수 없다."
                ) from None
            raise

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
            sht = self._sheet(sheet)
            nrows = min(sht.used_range.rows.count, max_rows)
            rng = sht.range(f"{column}1:{column}{nrows}")
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
                setup_error = f"{type(exc).__name__}: {exc}"
            if setup_error is not None:
                try:
                    app.quit()
                except Exception:  # noqa: BLE001
                    # quit() 자체의 실패로 진짜 원인(setup_error)이 가려지면
                    # 안 되므로 여기서는 삼킨다 — 유령 프로세스 방지가 목적이지
                    # quit() 성공 여부를 보고하는 게 목적이 아니다.
                    pass
                app = None
                raise RuntimeError(f"Excel App 초기화 실패: {setup_error}")
            return app

        app = executor.submit(_make_app).result()
        try:
            book = executor.submit(
                lambda: app.books.open(path, read_only=True, update_links=False)
            ).result()
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
                        close_error = f"{type(exc).__name__}: {exc}"
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
                    quit_error = f"{type(exc).__name__}: {exc}"
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
    "col_to_letter",
]
