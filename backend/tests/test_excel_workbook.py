import concurrent.futures

import pytest

xw = pytest.importorskip("xlwings")

from app.excel.workbook import (  # noqa: E402
    WorkbookCleanupError,
    WorkbookClosedError,
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
    tb = exc.__traceback__
    leaked: list[str] = []
    while tb is not None:
        frame = tb.tb_frame
        for var_name, var_val in frame.f_locals.items():
            if isinstance(var_val, (xw.Book, xw.App)):
                leaked.append(f"{frame.f_code.co_name}.{var_name} ({type(var_val).__name__})")
        tb = tb.tb_next
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
