import { create } from "zustand";
import { getFilterOptions, getMold, listMolds } from "../api/molds";
import type { FilterOptions, MoldDetail, MoldFilters, MoldSummary } from "../types/mold";

// 상태 기본값이 '사용중'인 이유: 화면을 열면 라인/호기 드롭다운이 바로 보여
// "3-2에 뭐가 걸렸지"를 한 번에 물을 수 있다. 현장에서 대부분의 관심은
// 사용중 금형이다.
export const DEFAULT_FILTERS: MoldFilters = {
  q: "",
  status: "in_use",
  line: null,
  machine: null,
};

interface DashboardState {
  filters: MoldFilters;
  options: FilterOptions | null;
  molds: MoldSummary[];
  detail: MoldDetail | null;
  listLoading: boolean;
  detailLoading: boolean;
  // 전체 리뷰에서 확인된 버그: 예전에는 error 필드 하나를 loadOptions /
  // loadMolds / loadDetail 세 loader 가 함께 썼다. 그 결과 아무 loader나
  // 성공하면 error: null 을 써서 다른 loader의 실패를 지워버렸다 — 예를
  // 들어 상세 조회가 404 로 실패한 채로 필터만 바꿔도(목록 조회 성공)
  // 상세 패널의 에러가 사라져 "불러오는 중…"에 영구히 멈췄다. 목록 쪽
  // (loadOptions, loadMolds)과 상세 쪽(loadDetail)의 실패는 서로 독립된
  // 필드에 적어 이 교차 오염을 없앤다.
  listError: string | null;
  detailError: string | null;
  // listError 는 loadOptions 와 loadMolds 가 함께 쓰는 필드다 — 필터
  // 선택지든 목록이든 실패하면 똑같이 "목록 화면이 온전하지 않다"는 뜻이라
  // 배너 하나로 합쳐 보여준다. 하지만 둘 중 하나의 성공이 다른 하나의
  // 살아있는 실패를 지워버리면 안 된다(예: loadOptions 가 404 로 실패한
  // 채로 필터가 바뀌어 loadMolds 가 성공하면, listError: null 을 무조건
  // 쓰는 순간 options 는 여전히 null 인데 에러 배너는 사라진다 — 상태
  // select 가 '전체'만 보여주면서도 화면은 조용하다). 그래서 두 loader
  // 각각의 최근 실패를 별도로 기억해뒀다가 listError 는 그 조합으로
  // 계산한다. 이 두 값은 DashboardState 밖의 클로저가 아니라 상태 필드로
  // 둔다 — 테스트의 beforeEach 가 setState 로 리셋할 수 있어야 하기
  // 때문이다. 어떤 컴포넌트도 이 두 필드를 직접 읽지 않는다(listError만
  // 읽는다).
  _optionsError: string | null;
  _moldsError: string | null;
  detailToken: number;

  setFilter: (patch: Partial<MoldFilters>) => void;
  resetFilters: () => void;
  loadOptions: () => Promise<void>;
  loadMolds: () => Promise<void>;
  loadDetail: (moldNo: string) => Promise<void>;
  clearDetail: () => void;
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function combineListError(moldsError: string | null, optionsError: string | null): string | null {
  if (moldsError && optionsError) return `${moldsError} / ${optionsError}`;
  return moldsError ?? optionsError;
}

export const useDashboardStore = create<DashboardState>((set, get) => ({
  filters: { ...DEFAULT_FILTERS },
  options: null,
  molds: [],
  detail: null,
  // 초기값을 true 로 둔다: 대시보드는 마운트되면 항상 loadMolds()를 호출하므로
  // "증명되기 전까지는 로딩 중"이 정직한 기본값이다. false 로 두면 목록이
  // 도착하기 전 한 프레임 동안 molds=[] 와 겹쳐 "조건에 맞는 금형이
  // 없습니다"+필터 초기화 버튼이 잘못 뜬다 — 실제로는 아무 필터도 문제가
  // 아닌데 사용자에게 필터를 고치라고 유도하는 오판이다.
  listLoading: true,
  detailLoading: false,
  listError: null,
  detailError: null,
  _optionsError: null,
  _moldsError: null,
  detailToken: 0,

  setFilter: (patch) =>
    set((s) => {
      const filters = { ...s.filters, ...patch };
      // 라인/호기는 상태에 종속이다. 상태가 '사용중'을 벗어나면 화면에서
      // 드롭다운이 사라지므로, 남은 값이 조용히 쿼리에 실려 0건이 되는 일을
      // 막기 위해 여기서 함께 비운다.
      if (filters.status !== "in_use") {
        filters.line = null;
        filters.machine = null;
      }
      return { filters };
    }),

  resetFilters: () => set({ filters: { ...DEFAULT_FILTERS } }),

  loadOptions: async () => {
    try {
      const options = await getFilterOptions();
      set({ options, _optionsError: null, listError: combineListError(get()._moldsError, null) });
    } catch (err) {
      const optionsError = message(err);
      set({ _optionsError: optionsError, listError: combineListError(get()._moldsError, optionsError) });
    }
  },

  loadMolds: async () => {
    set({ listLoading: true });
    try {
      const molds = await listMolds(get().filters);
      set({ molds, _moldsError: null, listError: combineListError(null, get()._optionsError) });
    } catch (err) {
      const moldsError = message(err);
      set({ _moldsError: moldsError, listError: combineListError(moldsError, get()._optionsError) });
    } finally {
      set({ listLoading: false });
    }
  },

  // 요청 토큰: 사용자가 금형을 빠르게 연달아 고르면 loadDetail 이 동시에 여러 번
  // 돈다. 먼저 보낸 요청이 나중에 응답하면 최신 선택을 덮어써, URL 은 B 인데
  // 화면은 A 를 보여주는 상태가 영구히 남는다. 토큰이 최신일 때만 반영한다.
  loadDetail: async (moldNo) => {
    const token = get().detailToken + 1;
    set({ detailToken: token, detailLoading: true });
    try {
      const detail = await getMold(moldNo);
      if (get().detailToken !== token) return; // 더 최신 요청이 있다 — 버린다
      set({ detail, detailError: null });
    } catch (err) {
      if (get().detailToken !== token) return;
      // 상세 조회 실패 시 옛 금형의 상세가 남아 있으면 다른 금형의 데이터를
      // 보고 있다고 오해하게 된다. 반드시 비운다. listError 는 건드리지
      // 않는다 — 상세 조회 실패는 목록 화면과 무관하다.
      set({ detail: null, detailError: message(err) });
    } finally {
      if (get().detailToken === token) set({ detailLoading: false });
    }
  },

  clearDetail: () => set({ detail: null }),
}));
