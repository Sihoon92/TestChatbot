import MessageList from "./MessageList";
import Composer from "./Composer";
import { useStore } from "../../store/store";

export default function ChatPanel() {
  const messages = useStore((s) => s.messages);
  const streaming = useStore((s) => s.streaming);
  return (
    <div className="flex h-full flex-1 flex-col">
      <MessageList messages={messages} streaming={streaming} />
      <Composer />
    </div>
  );
}
