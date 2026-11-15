/** Durations as people read them. */
import { describe, expect, it } from "vitest";
import { formatDuration } from "./format";

describe("formatDuration", () => {
  it("rounds whole minutes over, never to 60 seconds", () => {
    expect(formatDuration(850)).toBe("850 ms");
    expect(formatDuration(4200)).toBe("4.2 s");
    expect(formatDuration(125_000)).toBe("2 min 5 s");
    expect(formatDuration(119_600)).toBe("2 min 0 s");
    expect(formatDuration(179_700)).toBe("3 min 0 s");
  });
});
