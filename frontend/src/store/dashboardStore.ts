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
  error: string | null;

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

export const useDashboardStore = create<DashboardState>((set, get) => ({
  filters: { ...DEFAULT_FILTERS },
  options: null,
  molds: [],
  detail: null,
  listLoading: false,
  detailLoading: false,
  error: null,

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
      set({ options: await getFilterOptions() });
    } catch (err) {
      set({ error: message(err) });
    }
  },

  loadMolds: async () => {
    set({ listLoading: true });
    try {
      set({ molds: await listMolds(get().filters), error: null });
    } catch (err) {
      set({ error: message(err) });
    } finally {
      set({ listLoading: false });
    }
  },

  loadDetail: async (moldNo) => {
    set({ detailLoading: true });
    try {
      set({ detail: await getMold(moldNo), error: null });
    } catch (err) {
      // 상세 조회 실패 시 옛 금형의 상세가 남아 있으면 다른 금형의 데이터를
      // 보고 있다고 오해하게 된다. 반드시 비운다.
      set({ detail: null, error: message(err) });
    } finally {
      set({ detailLoading: false });
    }
  },

  clearDetail: () => set({ detail: null }),
}));
