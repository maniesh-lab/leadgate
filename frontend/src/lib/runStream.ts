/**
 * Reads a run's live stream (`GET /api/runs/{id}/events`, Server-Sent Events) with
 * `fetch`, so the login token goes in the Authorization header like every other call
 * (the browser's EventSource can't send headers, and a token in the URL would end up in
 * logs). Calls `onRun` with each new run detail; resolves when the server ends the
 * stream (the run finished, was deleted, or the 10-minute limit passed) and rejects if
 * it can't connect, so the caller can fall back to asking every few seconds.
 */

import { apiUrl } from "../api/client";
import type { RunDetail } from "../api/types";
import { getToken } from "../auth/token";

export type RunStreamEnd = "ended" | "gone";

export async function streamRun(
  runId: number,
  onRun: (run: RunDetail) => void,
  signal: AbortSignal,
): Promise<RunStreamEnd> {
  const token = getToken();
  const response = await fetch(apiUrl(`/api/runs/${runId}/events`), {
    headers: {
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    signal,
  });
  if (!response.ok || !response.body) throw new Error(`the stream answered ${response.status}`);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return "ended";
    buffer += decoder.decode(value, { stream: true });
    // Events are separated by a blank line.
    let end = buffer.indexOf("\n\n");
    while (end !== -1) {
      const event = parseEvent(buffer.slice(0, end));
      buffer = buffer.slice(end + 2);
      if (event?.name === "gone") return "gone";
      if (event?.name === "run") onRun(JSON.parse(event.data) as RunDetail);
      end = buffer.indexOf("\n\n");
    }
  }
}

/** One event block: `event: run` + `data: {...}` lines (`: ping` heartbeats give null). */
function parseEvent(block: string): { name: string; data: string } | null {
  let name = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data.push(line.slice(6));
  }
  return name ? { name, data: data.join("\n") } : null;
}
