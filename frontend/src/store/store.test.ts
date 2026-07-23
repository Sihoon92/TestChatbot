import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "./store";

describe("store", () => {
  beforeEach(() => {
    useStore.setState({ messages: [], sessions: [], activeSessionId: null, streaming: false });
  });

  it("appends a user message", () => {
    useStore.getState().appendUserMessage("hi");
    const msgs = useStore.getState().messages;
    expect(msgs).toEqual([{ role: "user", content: "hi" }]);
  });

  it("streams assistant deltas onto the last assistant message", () => {
    const st = useStore.getState();
    st.startAssistantMessage();
    st.appendAssistantDelta("Hel");
    st.appendAssistantDelta("lo");
    expect(useStore.getState().messages.at(-1)).toEqual({ role: "assistant", content: "Hello" });
  });

  it("replaces the last assistant content in one shot", () => {
    const st = useStore.getState();
    st.startAssistantMessage();
    st.setLastAssistantContent("**final**");
    expect(useStore.getState().messages.at(-1)?.content).toBe("**final**");
  });
});
