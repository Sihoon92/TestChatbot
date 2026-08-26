"""리포트 — 데이터가 부족하다는 결론도 반드시 산출물로 나와야 한다."""
from pathlib import Path

import pytest

from app.coating import parse, report

SAMPLE = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"
DICT = parse.DEFAULT_DICT_PATH


def test_profile_dataset_reports_verdict_on_sample():
    """샘플은 제어값이 하나도 안 변하는 안정 구간이다. 조정 이벤트 0건이면
    학습 불가로 판정하고 그 이유를 남겨야 한다."""
    facts = report.profile_dataset(SAMPLE, DICT)
    assert facts["n_lots"] == 1
    assert facts["n_events"] == 0
    assert facts["verdict"] == "insufficient"
    assert "조정 이벤트" in facts["verdict_reason"]


def test_profile_dataset_lists_missing_control_items():
    """RPM·BP open rate 가 없다는 사실이 리포트에 드러나야 한다 —
    설계상 가장 위험한 가정이다."""
    facts = report.profile_dataset(SAMPLE, DICT)
    assert "50030111" in facts["missing_control_items"]
    assert "10030009" in facts["missing_control_items"]


def test_render_markdown_contains_required_sections():
    facts = report.profile_dataset(SAMPLE, DICT)
    md = report.render_markdown(facts)
    for heading in ["lot 수", "조정 이벤트", "유효 랭크", "추가 데이터 요청"]:
        assert heading in md


def test_render_html_is_self_contained():
    """사업부에 파일 하나로 전달한다. 외부 리소스를 참조하면 안 열린다."""
    facts = report.profile_dataset(SAMPLE, DICT)
    html = report.render_html(facts)
    assert html.lstrip().startswith("<!doctype html")
    assert "<script src=" not in html
    assert "<link rel=\"stylesheet\"" not in html


def test_run_writes_both_files(tmp_path):
    md_path, html_path = report.run(SAMPLE, DICT, out_dir=tmp_path)
    assert Path(md_path).exists()
    assert Path(html_path).exists()


def test_cli_defaults_are_none_so_run_stays_single_source():
    """기본 경로를 파서에 또 적으면 run() 과 두 군데가 된다. 파서는 '지정 안 함'
    만 표현하고 기본값 결정은 run() 에 맡긴다."""
    args = report.build_parser().parse_args([])
    assert args.csv is None
    assert args.dict_path is None
    assert args.out_dir is None


def test_cli_writes_both_files_with_explicit_paths(tmp_path):
    """실데이터를 넣으려고 python -c 로 우회해야 하면 CLI 가 아니다."""
    md_path, html_path = report.main(
        ["--csv", str(SAMPLE), "--dict", str(DICT), "--out", str(tmp_path)]
    )
    assert Path(md_path).exists()
    assert Path(html_path).exists()
    assert Path(md_path).parent == tmp_path


def test_cli_reports_missing_csv_with_actionable_message(tmp_path, capsys):
    """기본 입력은 backend/data/ 아래인데 그건 gitignore 대상이라 새로 클론한
    곳에는 없다. 맨 트레이스백 대신 무엇을 하라는지 말해야 한다."""
    missing = tmp_path / "없는파일.csv"
    with pytest.raises(SystemExit) as e:
        report.main(["--csv", str(missing)])
    assert e.value.code != 0
    message = str(e.value) + capsys.readouterr().err
    assert "없는파일.csv" in message
    assert "--csv" in message
