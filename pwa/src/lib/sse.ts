import { API_BASE, getToken, type AgentEvent } from "./api";

export type StreamStatus = "connecting" | "connected" | "reconnecting" | "closed";

export interface StreamOptions {
  after?: number;
  onEvent: (event: AgentEvent) => void;
  onStatus?: (status: StreamStatus) => void;
}

export interface StreamHandle {
  close: () => void;
}

export function streamTaskEvents(taskId: string, opts: StreamOptions): StreamHandle {
  let closed = false;
  let last = opts.after ?? 0;
  let controller: AbortController | null = null;
  let attempt = 0;
  let timer: number | undefined;

  const setStatus = (s: StreamStatus) => {
    if (!closed || s === "closed") opts.onStatus?.(s);
  };

  const handleChunk = (chunk: string) => {
    let eventName = "message";
    let data = "";
    for (const rawLine of chunk.split("\n")) {
      const line = rawLine.replace(/\r$/, "");
      if (line.startsWith(":")) continue;
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (eventName === "event" && data) {
      let parsed: AgentEvent | null = null;
      try {
        parsed = JSON.parse(data) as AgentEvent;
      } catch {
        parsed = null;
      }
      if (parsed) {
        if (typeof parsed.seq === "number") last = Math.max(last, parsed.seq);
        if (!closed) opts.onEvent(parsed);
      }
    } else if (eventName === "end") {
      closed = true;
      controller?.abort();
      opts.onStatus?.("closed");
    }
  };

  const scheduleReconnect = () => {
    if (closed) return;
    setStatus("reconnecting");
    attempt += 1;
    const delay = Math.min(1000 * 2 ** Math.min(attempt, 5), 30000);
    timer = window.setTimeout(() => {
      void connect();
    }, delay);
  };

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    setStatus("connecting");
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/stream?after=${last}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
        signal: controller.signal,
      });
      if (!res.ok || !res.body) throw new Error(`stream failed (${res.status})`);
      setStatus("connected");
      attempt = 0;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx = buffer.indexOf("\n\n");
        while (idx !== -1) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          handleChunk(chunk);
          idx = buffer.indexOf("\n\n");
        }
      }
      if (!closed) scheduleReconnect();
    } catch {
      if (closed) return;
      scheduleReconnect();
    }
  };

  void connect();

  return {
    close() {
      closed = true;
      controller?.abort();
      if (timer) window.clearTimeout(timer);
      opts.onStatus?.("closed");
    },
  };
}
