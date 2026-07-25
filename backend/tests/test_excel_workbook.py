import concurrent.futures
import subprocess
import time

import pytest

xw = pytest.importorskip("xlwings")

from app.excel.workbook import (  # noqa: E402
    WorkbookCleanupError,
    WorkbookClosedError,
    WorkbookOperationError,
    open_workbook,
)


def _excel_available() -> bool:
    try:
        app = xw.App(visible=False)
        app.quit()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _excel_available(), reason="Excel COM 미설치")


def _leaked_xlwings_locals(exc: BaseException) -> list[str]:
    """예외의 traceback 프레임 지역변수 중 살아있는 xlwings 객체를 모두 찾는다.

    `type(var_val).__module__.startswith("xlwings")` 로 판정한다 — `xw.Book`/
    `xw.App` 뿐 아니라 `xw.Books`/`xw.Sheets`/`xw.Sheet`/`xw.Range` 등 이
    모듈이 다루는 모든 xlwings 래퍼 타입을 한 번에 잡기 위함이다(원래는
    `isinstance(var_val, (xw.Book, xw.App))` 만 검사했는데, 새로 고친 두
    지점(`_open_book`, `Workbook._run`)이 새는 타입은 `xw.Books`/`xw.Sheets`/
    `xw.Sheet`/`xw.Range` 라서 좁은 검사로는 회귀를 못 잡는다).

    `exc.__traceback__` 뿐 아니라 `__context__`/`__cause__` 체인도 재귀적으로
    walk 한다 — `raise ... from None` 은 `__suppress_context__` 만 세우고
    `__context__` 자체는 지우지 않으므로, 최상위 예외의 traceback 이 깨끗해도
    `__context__` 체인 어딘가에 원본 예외(및 그 안의 COM 프록시)가 그대로
    남아있을 수 있다(Critical 1 회귀: `raise WorkbookOperationError(...) from
    None` 이 `except` 블록 **안**에 있으면 바로 이 경로로 샌다).

    또한 `concurrent.futures.Future` 지역변수를 만나면 그 `_exception` 도
    재귀적으로 walk 한다 — `_WorkItem.run` 이 워커 스레드에서 발생한 예외를
    `future._exception` 에 저장해두므로, (수정 전 버전처럼) `_run` 이 호출자
    스레드에서 `future.result()` 를 캐치해 처리하는 구조라면 그 `future` 가
    `_run` 프레임의 지역변수로 남고, `future._exception` 은 (새로 던져진
    예외가 아니라) COM 프록시를 물고 있는 **원본** 예외를 계속 붙들고 있을 수
    있다(Critical 2). 다만 단순히 `_exception` 이 설정돼 있다는 사실 자체는
    정상이다 — `future.result()` 가 예외를 재던진 뒤에도 `Future` 객체는
    `_exception` 속성을 계속 들고 있고, 지금 바로 이 `exc`(walk 중인 최상위
    예외) 도 대개 그 값과 동일한 객체다. 그래서 `inner_exc is not e` 로
    "이미 지금 보고 있는 바로 그 예외"로의 사소한 자기참조는 건너뛰고, 그
    외의(=진짜로 다른, 아직 안 걸러진) 예외만 재귀적으로 walk 해 그 안에
    COM 프록시가 있으면 잡아낸다.
    """
    leaked: list[str] = []
    seen_ids: set[int] = set()

    def _walk(e: BaseException | None, chain: str) -> None:
        if e is None or id(e) in seen_ids:
            return
        seen_ids.add(id(e))
        tb = e.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            for var_name, var_val in frame.f_locals.items():
                if type(var_val).__module__.startswith("xlwings"):
                    leaked.append(
                        f"[{chain}] {frame.f_code.co_name}.{var_name} ({type(var_val).__name__})"
                    )
                if isinstance(var_val, concurrent.futures.Future):
                    inner_exc = var_val._exception  # noqa: SLF001
                    if inner_exc is not None and inner_exc is not e:
                        _walk(inner_exc, f"{chain}.future({var_name})._exception")
            tb = tb.tb_next
        _walk(e.__cause__, f"{chain}.__cause__")
        _walk(e.__context__, f"{chain}.__context__")

    _walk(exc, "exc")
    return leaked


def _excel_process_count() -> int:
    """현재 떠 있는 EXCEL.EXE 프로세스 수. 유령 프로세스 검증(전후 비교)에 쓴다."""
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.upper().count("EXCEL.EXE")


def _wait_for_excel_process_count(expected: int, timeout: float = 3.0) -> int:
    """`app.quit()` 이후 EXCEL.EXE 개수가 `expected` 로 수렴할 때까지 최대
    `timeout` 초 폴링한다.

    `app.quit()` 은 Excel 프로세스 종료를 기다리지 않고 비동기로 반환한다 —
    호출 직후 한 번만 `tasklist` 를 찍으면 프로세스가 아직 종료 중이라
    `after == before` 비교가 양방향으로 flaky 해진다(과다 카운트로 거짓
    실패, 혹은 드물게 다른 타이밍에 거짓 통과). 짧은 간격으로 재시도해
    수렴을 기다린다.
    """
    deadline = time.monotonic() + timeout
    count = _excel_process_count()
    while count != expected and time.monotonic() < deadline:
        time.sleep(0.2)
        count = _excel_process_count()
    return count


def test_open_and_read(tmp_path):
    path = tmp_path / "sample.xlsx"
    app = xw.App(visible=False)
    try:
        wb = app.books.add()
        sht = wb.sheets[0]
        sht.name = "데이터"
        sht.range("A1").value = [["라인", "제품", "2026-01-01"], ["A", "제품1", 3]]
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()

    with open_workbook(str(path)) as book:
        assert "데이터" in book.sheet_names()
        rows, top_left = book.used_values("데이터")
        assert top_left == "A1"
        assert rows[0][0] == "라인"
        assert book.column_values("데이터", "A")[:2] == ["라인", "A"]


def _write_sample(tmp_path, values):
    """values 를 시트 '데이터' 의 A1 에 쓴 새 워크북을 만들고 경로를 반환."""
    path = tmp_path / "shapes.xlsx"
    app = xw.App(visible=False)
    try:
        wb = app.books.add()
        sht = wb.sheets[0]
        sht.name = "데이터"
        if values is not None:
            sht.range("A1").value = values
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()
    return path


def test_used_shape_and_range_values_multi_row_multi_col(tmp_path):
    path = _write_sample(
        tmp_path, [["라인", "제품", "수량"], ["A", "제품1", 3], ["B", "제품2", 5]]
    )
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (3, 3)
        block = book.range_values("데이터", "A1:C3")
        assert block == [
            ["라인", "제품", "수량"],
            ["A", "제품1", 3.0],
            ["B", "제품2", 5.0],
        ]


def test_range_values_single_row_is_2d_one_row(tmp_path):
    path = _write_sample(tmp_path, [["라인", "제품", "수량"]])
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (1, 3)
        row = book.range_values("데이터", "A1:C1")
        assert row == [["라인", "제품", "수량"]]
        assert len(row) == 1
        assert len(row[0]) == 3


def test_range_values_single_column_is_2d_one_col(tmp_path):
    path = _write_sample(tmp_path, [["라인"], ["A"], ["B"]])
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (3, 1)
        col = book.range_values("데이터", "A1:A3")
        assert col == [["라인"], ["A"], ["B"]]
        assert len(col) == 3
        assert all(len(r) == 1 for r in col)


def test_range_values_single_cell_is_2d_scalar(tmp_path):
    path = _write_sample(tmp_path, [["단일값"]])
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (1, 1)
        cell = book.range_values("데이터", "A1")
        assert cell == [["단일값"]]


def test_range_values_empty_range_is_2d_none(tmp_path):
    path = _write_sample(tmp_path, [["라인", "제품"]])
    with open_workbook(str(path)) as book:
        # 데이터가 전혀 없는 셀을 명시적으로 읽으면 xlwings 는 None(스칼라)을
        # 돌려주고, ndim=2 정규화 계약상 [[None]] 이어야 한다.
        cell = book.range_values("데이터", "E10")
        assert cell == [[None]]


def test_used_values_and_column_values_single_row_sheet(tmp_path):
    path = _write_sample(tmp_path, [["라인", "제품", "수량"]])
    with open_workbook(str(path)) as book:
        rows, top_left = book.used_values("데이터")
        assert top_left == "A1"
        assert rows == [["라인", "제품", "수량"]]
        assert book.column_values("데이터", "A") == ["라인"]
        assert book.column_values("데이터", "B") == ["제품"]


def test_open_workbook_from_worker_thread(tmp_path):
    """open_workbook 자체를 메인이 아닌 스레드에서 열어도 동작해야 한다.

    (참고) 이 테스트만으로는 실제 버그를 재현하지 못한다: xlwings 는
    `xw.App()` 생성 시점에 내부적으로 `pythoncom.CoInitialize()` 를 이미
    호출하므로, open 과 read 가 '같은' 스레드 안에서 끝나면 그 호출이 COM 을
    초기화해준다. 진짜 버그는 open 한 스레드와 값을 읽는 스레드가 다를 때
    발생한다 — 아래 test_open_on_one_thread_read_on_another 참고. 이 테스트는
    "어느 스레드에서 열든 동작해야 한다"는 요구사항 자체의 회귀 테스트로 남긴다.
    """
    path = _write_sample(
        tmp_path, [["라인", "제품", "수량"], ["A", "제품1", 3], ["B", "제품2", 5]]
    )

    def _read_in_worker():
        with open_workbook(str(path)) as book:
            assert "데이터" in book.sheet_names()
            rows, top_left = book.used_values("데이터")
            return rows, top_left, book.used_shape("데이터")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        rows, top_left, shape = executor.submit(_read_in_worker).result()

    assert top_left == "A1"
    assert shape == (3, 3)
    assert rows == [
        ["라인", "제품", "수량"],
        ["A", "제품1", 3.0],
        ["B", "제품2", 5.0],
    ]


def test_cleanup_severs_references_and_quits_app_when_close_raises(tmp_path, monkeypatch):
    """Critical 1 회귀 테스트: book.close() 가 예외를 던져도 app.quit() 은 실행되고,
    (wb._book/제너레이터 프레임의) 참조는 끊겨야 한다. 또한 호출자 스레드로
    전파되는 예외는 원본 xlwings 예외 객체가 아니라 새로 만들어진
    WorkbookCleanupError 여야 한다 — 원본을 그대로(`raise`) 전파하면
    concurrent.futures 가 그 __traceback__ 을 프레임째 호출자 스레드로 넘기고,
    그 프레임들(xlwings Book.close 내부의 self 등)이 살아있는 xw.Book/xw.App
    COM 프록시를 들고 있어 그 프록시의 마지막 릴리스가 호출자 스레드(전용
    워커가 join 되고 CoUninitialize 된 뒤)에서 실행되는, 이 모듈이 곳곳에서
    막고 있는 바로 그 크로스 아파트먼트 릴리스가 재현된다.

    close() 가 실패했는데 참조를 못 끊으면, 이 전용 워커 스레드가 join 되고
    CoUninitialize 된 뒤 호출자 스레드에서 COM 프록시가 파이널라이즈되며
    0x800401F0(CO_E_NOTINITIALIZED) 이 재현된다 — 이 테스트는 그 경로로 가지
    않는지를 확인한다.
    """
    path = _write_sample(tmp_path, [["라인", "제품"], ["A", "제품1"]])

    def _raising_close(self):
        raise RuntimeError("close 실패 시뮬레이션")

    monkeypatch.setattr(xw.Book, "close", _raising_close)

    quit_calls: list[bool] = []
    orig_quit = xw.App.quit

    def _tracking_quit(self):
        quit_calls.append(True)
        return orig_quit(self)

    monkeypatch.setattr(xw.App, "quit", _tracking_quit)

    wb_ref = None
    with pytest.raises(WorkbookCleanupError, match="close 실패 시뮬레이션") as exc_info:
        with open_workbook(str(path)) as wb:
            wb_ref = wb
            assert "데이터" in wb.sheet_names()  # close() 전에는 정상 동작

    # close() 가 실패했더라도 app.quit() 은 바깥 finally 에서 여전히 호출돼야
    # 한다 — "유령 프로세스 방지" 제약을 지키는 부분.
    assert quit_calls, "app.quit() 가 book.close() 실패에도 호출되어야 한다"

    # book.close() 가 실패해도 참조 끊기(wb._book = None)는 try/finally 로
    # 보장돼야 한다.
    assert wb_ref is not None
    assert wb_ref._book is None

    # Critical 1 핵심 검증: 호출자 스레드로 넘어온 예외의 traceback 프레임
    # 어디에도 살아있는 xw.Book/xw.App COM 프록시가 지역변수로 남아있으면 안
    # 된다 — 남아있다면 그 프록시가 워커 스레드가 아니라 이 (호출자) 스레드에서
    # 파이널라이즈된다는 뜻이고, 그게 바로 이 회귀가 막으려는 크로스
    # 아파트먼트 릴리스다.
    exc = exc_info.value
    leaked = _leaked_xlwings_locals(exc)
    assert not leaked, f"호출자 스레드로 전파된 예외의 traceback 에 COM 객체가 남아있다: {leaked}"

    # __context__ 체이닝을 통해서도 원본 예외(및 그 traceback)가 새어나가지
    # 않아야 한다 — WorkbookCleanupError 는 except 블록 밖에서 새로 던져지므로
    # __context__ 가 없어야 한다.
    assert exc.__context__ is None


def test_app_quits_when_context_body_raises(tmp_path, monkeypatch):
    """with 블록 본문에서 예외가 나도 app.quit() 은 실행돼 유령 프로세스가 남지
    않아야 한다."""
    path = _write_sample(tmp_path, [["라인", "제품"], ["A", "제품1"]])

    quit_calls: list[bool] = []
    orig_quit = xw.App.quit

    def _tracking_quit(self):
        quit_calls.append(True)
        return orig_quit(self)

    monkeypatch.setattr(xw.App, "quit", _tracking_quit)

    class _BodyBoom(Exception):
        pass

    with pytest.raises(_BodyBoom):
        with open_workbook(str(path)) as wb:
            wb.sheet_names()  # 컨텍스트가 정상 동작 중임을 확인
            raise _BodyBoom("본문에서 실패")

    assert quit_calls, "with 블록 본문에서 예외가 나도 app.quit() 이 호출돼야 한다"


def test_make_app_quits_ghost_process_when_setup_raises(tmp_path, monkeypatch):
    """Minor 3 회귀 테스트: _make_app 내부에서 app.display_alerts 대입이 실패해도
    (그 시점에 이미 떠 있는) EXCEL.EXE 프로세스에 app.quit() 이 호출돼야 한다 —
    안 그러면 아무도 정리하지 못하는 유령 프로세스가 남는다."""
    path = _write_sample(tmp_path, [["라인", "제품"], ["A", "제품1"]])

    quit_calls: list[bool] = []
    orig_quit = xw.App.quit

    def _tracking_quit(self):
        quit_calls.append(True)
        return orig_quit(self)

    monkeypatch.setattr(xw.App, "quit", _tracking_quit)

    orig_display_alerts = xw.App.display_alerts  # property, .fget 은 그대로 재사용

    def _raising_setter(self, value):
        raise RuntimeError("display_alerts 대입 실패 시뮬레이션")

    monkeypatch.setattr(
        xw.App, "display_alerts", property(orig_display_alerts.fget, _raising_setter)
    )

    with pytest.raises(RuntimeError, match="Excel App 초기화 실패"):
        with open_workbook(str(path)):
            pass  # _make_app 단계에서 이미 실패하므로 본문은 실행되지 않는다

    assert quit_calls, "app.quit() 가 _make_app 초기화 실패에도 호출되어야 한다"


def test_workbook_method_after_close_raises_clear_domain_error(tmp_path):
    """Minor 9 회귀 테스트: with 블록을 벗어난 뒤 Workbook 메서드를 호출하면
    "cannot schedule new futures after shutdown" 같은 내부 구현 디테일이 아니라
    명확한 WorkbookClosedError 가 나야 한다 (LangGraph 실행이 with 블록보다
    오래 살아남는 시나리오)."""
    path = _write_sample(tmp_path, [["라인", "제품"], ["A", "제품1"]])

    with open_workbook(str(path)) as wb:
        assert "데이터" in wb.sheet_names()

    with pytest.raises(WorkbookClosedError):
        wb.sheet_names()
    with pytest.raises(WorkbookClosedError):
        wb.used_shape("데이터")
    with pytest.raises(WorkbookClosedError):
        wb.range_values("데이터", "A1")
    with pytest.raises(WorkbookClosedError):
        wb.used_values("데이터")
    with pytest.raises(WorkbookClosedError):
        wb.column_values("데이터", "A")


def test_open_on_one_thread_read_on_another(tmp_path):
    """실제 관측된 실패를 재현한다: open_workbook 은 스레드 A 에서, 읽기는 스레드 B 에서.

    이것이 사전점검 [3]/`analyze_excel.py` 의 실제 실행 형태다: `open_workbook`
    으로 워크북을 한 번 연 뒤(호출자 스레드), 그 `wb` 에 바인딩된 도구들은
    LangGraph 가 내부적으로 별도의 ThreadPoolExecutor 워커 스레드에서 실행한다
    (직접 확인함: create_react_agent 로 만든 그래프를 동기 invoke() 해도 도구
    본문은 'ThreadPoolExecutor-N_0' 같은 스레드에서 실행되지, invoke() 를 부른
    스레드가 아니다). 이때 COM 프록시(App/Book)는 open 한 스레드(A)의 STA 에
    속해 있는데, 읽기 호출은 스레드 B 에서 일어난다.

    수정 전에는 이 패턴이 -2147221008 ('CoInitialize가 호출되지 않았습니다')
    로 실패한다(이 스레드는 COM 을 전혀 초기화한 적이 없으므로). 스레드 B 에서
    단순히 pythoncom.CoInitialize() 만 호출해도 고쳐지지 않는다 — 그러면 오류가
    -2147417842(RPC_E_WRONG_THREAD, '다른 스레드를 위해 배열된 인터페이스를
    호출')로 바뀔 뿐이다: A 에서 만든 COM 포인터를 B 에서 그대로 쓰는 것 자체가
    문제이기 때문이다(둘 다 실측으로 확인함). 그래서 올바른 고침은 이 워크북의
    모든 COM 호출을 전용 워커 스레드 하나로 위임하는 것이다(open 한 호출자
    스레드가 무엇이든, 이후 읽기가 어느 스레드에서 오든).
    """
    path = _write_sample(
        tmp_path, [["라인", "제품", "수량"], ["A", "제품1", 3], ["B", "제품2", 5]]
    )

    with open_workbook(str(path)) as book:  # 이 테스트 함수의 스레드(=메인)에서 open
        assert "데이터" in book.sheet_names()

        def _read_in_other_thread():
            rows, top_left = book.used_values("데이터")
            return rows, top_left, book.used_shape("데이터")

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            rows, top_left, shape = executor.submit(_read_in_other_thread).result()

    assert top_left == "A1"
    assert shape == (3, 3)
    assert rows == [
        ["라인", "제품", "수량"],
        ["A", "제품1", 3.0],
        ["B", "제품2", 5.0],
    ]


def test_open_nonexistent_path_raises_clear_error_without_leak_or_orphan(tmp_path):
    """Critical 회귀 테스트: app.books.open() 이 실패하는(존재하지 않는 경로) 가장
    흔한 나쁜 입력이 발생했을 때, `_open_book` 이 원본 xlwings 예외를 렌더링만
    해서 새 RuntimeError 로 다시 던지는지 검증한다. `_make_app` 과 동일한
    크로스 아파트먼트 위험이 세 줄 옆(`app.books.open(...)`)에도 있었다 —
    원본을 그대로 재던졌다면 그 예외의 traceback 프레임(Books.open 내부의
    self=xw.Books/xw.App 등)에 살아있는 COM 프록시가 호출자 스레드까지
    실려갔을 것이다. 유령 EXCEL.EXE 도 남지 않아야 한다(app.quit() 은 바깥
    finally 에서 여전히 보장된다)."""
    missing_path = tmp_path / "does_not_exist.xlsx"

    before = _excel_process_count()
    with pytest.raises(RuntimeError, match="워크북 열기 실패") as exc_info:
        with open_workbook(str(missing_path)):
            pass  # _open_book 단계에서 이미 실패하므로 본문은 실행되지 않는다

    leaked = _leaked_xlwings_locals(exc_info.value)
    assert not leaked, f"호출자 스레드로 전파된 예외의 traceback 에 COM 객체가 남아있다: {leaked}"

    # `_open_book` 은 except 블록 밖에서 완전히 새 RuntimeError 를 던지므로
    # __context__ 가 없어야 한다 — 세 곳(`_open_book`/`_cleanup`(아래 close
    # 실패 테스트)/`Workbook._run`) 모두 같은 불변식으로 고정한다.
    assert exc_info.value.__context__ is None

    # app.quit() 은 비동기로 반환하므로 (Windows 프로세스 종료는 즉시가
    # 아니다), 한 번만 찍으면 양방향으로 flaky 하다 — 수렴할 때까지 폴링한다.
    after = _wait_for_excel_process_count(before)
    assert after == before, "테스트 종료 후 유령 EXCEL.EXE 프로세스가 남았다"


def test_bad_sheet_name_raises_workbook_operation_error_without_leak(tmp_path):
    """Important 회귀 테스트: 살아있는 컨텍스트 안에서 존재하지 않는 시트 이름으로
    `Workbook` 메서드를 호출하면 `WorkbookOperationError` 로 감싸져야 한다.
    `app/excel/tools.py` 는 `wb.*` 호출을 (aggregate 를 빼면) 전혀 try/except
    로 감싸지 않으므로, LLM 이 지어낸 잘못된 시트 이름은 이 경로를 그대로
    타고 나온다. `self._book.sheets[sheet]`(`Workbook._sheet`)가 실패하면 그
    예외는 xlwings 내부 프레임(`Sheets.__getitem__` 등)에 `self`(xw.Sheets)를
    물고 있으므로, `Workbook._run` 이 렌더링 없이 원본을 그대로 재던졌다면
    그 COM 프록시가 호출자 스레드까지 실려갔을 것이다."""
    path = _write_sample(tmp_path, [["라인", "제품"], ["A", "제품1"]])

    with open_workbook(str(path)) as wb:
        with pytest.raises(WorkbookOperationError) as exc_info:
            wb.used_shape("존재하지않는시트")

        # with 블록을 벗어나기 전, 컨텍스트가 여전히 정상 동작하는지 확인
        # (WorkbookOperationError 가 워크북 자체를 망가뜨리지 않아야 한다).
        assert "데이터" in wb.sheet_names()

    message = str(exc_info.value)
    # 원본 예외의 타입 이름이 (렌더링이 아니라 짧게 자른) 메시지에도 텍스트로
    # 보존돼 있어야 최소한의 진단이 가능하다. 실측: 존재하지 않는 시트 이름을
    # 조회하면 xlwings 가 COM 호출(Sheets.__call__ -> InvokeTypes) 단계에서
    # 실패해 원본 예외 타입은 `pywintypes.com_error` 다 — `len(message) > 40`
    # 같은 느슨한 길이 체크는 메시지를 짧게 자른 뒤(LLM 프롬프트 보호)에는
    # 의미가 없으므로, 구체적인 원본 타입 이름 포함 여부로 바꾼다.
    assert "엑셀 작업 실패" in message
    assert "com_error" in message, f"원본 예외 타입 이름이 메시지에 보존돼야 한다: {message!r}"
    assert len(message) < 400, "LLM 프롬프트로 들어가는 메시지는 짧게 잘려야 한다"

    # 전체 렌더(전체 traceback)는 메시지가 아니라 note 로만 붙는다 — `ToolNode`
    # 의 `handle_tool_errors=True` 가 담는 `repr(e)` 에는 note 가 포함되지
    # 않으므로 프롬프트는 짧게 유지되지만, 오퍼레이터/로그에서는 여전히 전체
    # 정보를 볼 수 있어야 한다.
    notes = "".join(getattr(exc_info.value, "__notes__", []))
    assert "com_error" in notes and "xlwings" in notes, (
        f"전체 traceback 렌더가 note 로 보존돼야 한다: {notes!r}"
    )

    # Critical 1 회귀 검증: `_run` 이 워커 스레드에서 (except 블록 밖에서) 새로
    # 던지므로 __context__ 가 없어야 한다. 예전 버전은
    # `raise WorkbookOperationError(...) from None` 을 `except` 블록 **안**에
    # 두고 있어서, `__suppress_context__` 만 세워지고 `__context__` 자체는
    # (원본 pywintypes.com_error 와 그 안의 xlwings COM 프록시 프레임까지)
    # 그대로 남아있었다 — 아래 assert 가 없으면 이 회귀를 못 잡는다.
    assert exc_info.value.__context__ is None

    leaked = _leaked_xlwings_locals(exc_info.value)
    assert not leaked, f"호출자 스레드로 전파된 예외의 traceback 에 COM 객체가 남아있다: {leaked}"
