/** The score ramp: one orange step per 20 points, the dashboard's buckets. */
import { describe, expect, it } from "vitest";
import { scoreColor } from "./score";

describe("scoreColor", () => {
  it("gives each 20-point bucket its own step", () => {
    expect([0, 19, 20, 45, 79, 80, 100].map(scoreColor)).toEqual([
      "var(--score-1)",
      "var(--score-1)",
      "var(--score-2)",
      "var(--score-3)",
      "var(--score-4)",
      "var(--score-5)",
      "var(--score-5)",
    ]);
  });
});
