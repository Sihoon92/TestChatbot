"""대시보드 화면 개발·검증용 고정 샘플 금형.

서브프로젝트 ②(파일 감시) · ③(AI 추출) · ④(영속화)가 붙기 전까지 API 가
돌려주는 데이터다. 화면이 그려야 하는 모든 상태(대기중/수리중, 단계 누락,
추출 실패, null 설계값, 생산 이력 0건)를 의도적으로 포함한다 — 정상 데이터만
있으면 빈 상태·에러 UI 를 손으로 확인할 방법이 없다.
"""
from app.molds.schemas import (
    CumulativeHistory,
    CurrentState,
    DefectRate,
    DesignSpec,
    MoldDetail,
    MoldSummary,
    ProductionRun,
    SourceRef,
    StageItem,
    StagePanel,
)

SAMPLE_MOLDS: list[MoldDetail] = [
    # ── 정상 금형: 전 단계 ok, 생산 이력 3건
    MoldDetail(
        summary=MoldSummary(
            mold_no="RX28312",
            status="in_use",
            line="3",
            machine="2",
            shot_count=8412,
            latest_defect_rate=0.008,
            total_production=1_204_500,
            stage_status={
                "design": "ok", "iqc": "ok", "pqc": "ok",
                "install": "ok", "ai_recheck": "ok",
            },
        ),
        design=DesignSpec(
            angle_deg=12.5, height_mm=45.0, step_mm=0.8,
            overall_mm=210.0, plate_height_mm=120.0, plate_width_mm=80.0,
        ),
        history=CumulativeHistory(
            total_installs=37, total_production=1_204_500,
            first_installed_at="2023-04-11",
        ),
        current=CurrentState(
            status="in_use", line="3", machine="2",
            shot_count=8412, installed_at="2026-07-20",
        ),
        productions=[
            ProductionRun(
                install_seq=37, line="3", machine="2",
                started_at="2026-07-20", ended_at=None,
                grind_result="OK", defect_rate=0.008,
                defects=[DefectRate(label="버", rate=0.003),
                         DefectRate(label="크랙", rate=0.002)],
            ),
            ProductionRun(
                install_seq=36, line="1", machine="4",
                started_at="2026-07-10", ended_at="2026-07-19",
                grind_result="재연마", defect_rate=0.021,
                defects=[DefectRate(label="버", rate=0.012),
                         DefectRate(label="크랙", rate=0.005),
                         DefectRate(label="미성형", rate=0.004)],
            ),
            ProductionRun(
                install_seq=35, line="3", machine="2",
                started_at="2026-06-28", ended_at="2026-07-09",
                grind_result="OK", defect_rate=0.006,
                defects=[DefectRate(label="버", rate=0.006)],
            ),
        ],
        stages=[
            StagePanel(
                stage="iqc", status="ok", updated_at="2026-07-03",
                items=[
                    StageItem(label="경도", value="HRC 58", judgment="ok",
                              source=SourceRef(file="IQC/2026-07-03_RX28312.xlsx",
                                               sheet="검사", cell="C12")),
                    StageItem(label="표면조도", value="Ra 0.4", judgment="ok",
                              source=SourceRef(file="IQC/2026-07-03_RX28312.xlsx",
                                               sheet="검사", cell="C13")),
                ],
            ),
            StagePanel(
                stage="pqc", status="ok", updated_at="2026-07-19",
                items=[
                    StageItem(label="치수 검사", value="12건 / NG 0건", judgment="ok",
                              source=SourceRef(file="PQC/2026-07-19_RX28312.xlsx",
                                               sheet="공정", cell="B5")),
                ],
            ),
            StagePanel(
                stage="ai_recheck", status="ok", updated_at="2026-07-21",
                items=[
                    StageItem(label="복검 판정", value="합격", judgment="ok",
                              source=SourceRef(file="AI복검/2026-07-21_RX28312.xlsx",
                                               sheet="결과", cell="D2")),
                ],
            ),
        ],
    ),
    # ── AI복검 문서가 아직 없는 금형(missing 배지 확인용)
    MoldDetail(
        summary=MoldSummary(
            mold_no="RX28315",
            status="in_use",
            line="3",
            machine="5",
            shot_count=2109,
            latest_defect_rate=0.014,
            total_production=402_100,
            stage_status={
                "design": "ok", "iqc": "ok", "pqc": "ok",
                "install": "ok", "ai_recheck": "missing",
            },
        ),
        design=DesignSpec(
            angle_deg=9.0, height_mm=38.5, step_mm=0.5,
            overall_mm=180.0, plate_height_mm=100.0, plate_width_mm=70.0,
        ),
        history=CumulativeHistory(
            total_installs=11, total_production=402_100,
            first_installed_at="2025-09-02",
        ),
        current=CurrentState(
            status="in_use", line="3", machine="5",
            shot_count=2109, installed_at="2026-07-22",
        ),
        productions=[
            ProductionRun(
                install_seq=11, line="3", machine="5",
                started_at="2026-07-22", ended_at=None,
                grind_result="OK", defect_rate=0.014,
                defects=[DefectRate(label="버", rate=0.009),
                         DefectRate(label="스크래치", rate=0.005)],
            ),
        ],
        stages=[
            StagePanel(
                stage="iqc", status="ok", updated_at="2025-09-01",
                items=[StageItem(label="경도", value="HRC 56", judgment="ok",
                                 source=SourceRef(file="IQC/2025-09-01_RX28315.xlsx",
                                                  sheet="검사", cell="C12"))],
            ),
            StagePanel(
                stage="pqc", status="ok", updated_at="2026-07-21",
                items=[StageItem(label="치수 검사", value="12건 / NG 1건", judgment="ng",
                                 source=SourceRef(file="PQC/2026-07-21_RX28315.xlsx",
                                                  sheet="공정", cell="B5"))],
            ),
            StagePanel(stage="ai_recheck", status="missing", items=[]),
        ],
    ),
    # ── 대기중 + PQC 추출 실패 + 설계값 일부 null (error 배지 · `—` 렌더 확인용)
    MoldDetail(
        summary=MoldSummary(
            mold_no="RX41194",
            status="standby",
            line=None,
            machine=None,
            shot_count=0,
            latest_defect_rate=0.005,
            total_production=2_811_300,
            stage_status={
                "design": "ok", "iqc": "ok", "pqc": "error",
                "install": "ok", "ai_recheck": "missing",
            },
        ),
        design=DesignSpec(
            angle_deg=None, height_mm=52.0, step_mm=None,
            overall_mm=240.0, plate_height_mm=140.0, plate_width_mm=None,
        ),
        history=CumulativeHistory(
            total_installs=64, total_production=2_811_300,
            first_installed_at="2021-02-15",
        ),
        current=CurrentState(
            status="standby", line=None, machine=None,
            shot_count=0, installed_at=None,
        ),
        productions=[
            ProductionRun(
                install_seq=64, line="1", machine="4",
                started_at="2026-05-02", ended_at="2026-06-30",
                grind_result="OK", defect_rate=0.005,
                defects=[DefectRate(label="버", rate=0.005)],
            ),
            ProductionRun(
                install_seq=63, line="1", machine="4",
                started_at="2026-03-11", ended_at="2026-04-28",
                grind_result="재연마", defect_rate=0.031,
                defects=[DefectRate(label="버", rate=0.020),
                         DefectRate(label="크랙", rate=0.011)],
            ),
        ],
        stages=[
            StagePanel(
                stage="iqc", status="ok", updated_at="2021-02-14",
                items=[StageItem(label="경도", value="HRC 60", judgment="ok",
                                 source=SourceRef(file="IQC/2021-02-14_RX41194.xlsx",
                                                  sheet="검사", cell="C12"))],
            ),
            StagePanel(
                stage="pqc", status="error",
                error="시트 '공정'을 찾지 못했습니다 (PQC/2026-06-30_RX41194.xlsx)",
                items=[],
            ),
            StagePanel(stage="ai_recheck", status="missing", items=[]),
        ],
    ),
    # ── 수리중 + 생산 이력 0건 (빈 표 확인용)
    MoldDetail(
        summary=MoldSummary(
            mold_no="RX39002",
            status="repair",
            line=None,
            machine=None,
            shot_count=None,          # 미상 — 0(신품)이 아니다
            latest_defect_rate=None,
            total_production=None,
            stage_status={
                "design": "ok", "iqc": "missing", "pqc": "missing",
                "install": "missing", "ai_recheck": "missing",
            },
        ),
        design=DesignSpec(
            angle_deg=15.0, height_mm=40.0, step_mm=1.2,
            overall_mm=195.0, plate_height_mm=110.0, plate_width_mm=75.0,
        ),
        history=CumulativeHistory(
            total_installs=None, total_production=None, first_installed_at=None,
        ),
        current=CurrentState(
            status="repair", line=None, machine=None,
            shot_count=None, installed_at=None,
        ),
        productions=[],
        stages=[
            StagePanel(stage="iqc", status="missing", items=[]),
            StagePanel(stage="pqc", status="missing", items=[]),
            StagePanel(stage="ai_recheck", status="missing", items=[]),
        ],
    ),
]
