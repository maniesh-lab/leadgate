/** Reading the run stream: events, heartbeats, events split across chunks, and "gone". */
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunDetail } from "../api/types";
import { streamRun } from "./runStream";

afterEach(() => vi.unstubAllGlobals());

function chunked(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, { status: 200 });
}

describe("streamRun", () => {
  it("passes each run on, skips heartbeats, and copes with split chunks", async () => {
    const fetchMock = vi.fn(async () =>
      chunked([
        'event: run\ndata: {"id": 7, "status": "run',
        'ning"}\n\n: ping\n\n',
        'event: run\ndata: {"id": 7, "status": "completed"}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const seen: string[] = [];

    const end = await streamRun(
      7,
      (run: RunDetail) => seen.push(run.status),
      new AbortController().signal,
    );

    expect(end).toBe("ended");
    expect(seen).toEqual(["running", "completed"]);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/runs/7/events");
    expect(new URLSearchParams(url.split("?")[1] ?? "").has("token")).toBe(false);
    expect((init.headers as Record<string, string>).Accept).toBe("text/event-stream");
  });

  it("says when the run is gone, and fails when it can't connect", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => chunked(["event: gone\ndata: {}\n\n"])),
    );
    expect(await streamRun(7, () => {}, new AbortController().signal)).toBe("gone");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("no", { status: 401 })),
    );
    await expect(streamRun(7, () => {}, new AbortController().signal)).rejects.toThrow("401");
  });
});
