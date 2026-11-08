/** The score card: the rubric line, and "not checked" on a category rule with nothing to
 * compare. */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Review } from "../api/types";
import { ScoreReview } from "./ScoreReview";

function review(categoryChecked: boolean | null): Review {
  return {
    rubric: { id: 1, name: "general-b2b", version: 1, description: "For general outreach." },
    category_checked: categoryChecked,
    score_breakdown: [
      { rule: "has phone", field: "phone", points: 15, hit: true },
      { rule: "category matches niche", field: "category_matches_niche", points: 15, hit: true },
    ],
    score_explanation: null,
    extraction_issues: [],
  };
}

describe("ScoreReview", () => {
  it("says the category wasn't checked when there was nothing to compare", () => {
    render(<ScoreReview review={review(false)} score={30} />);

    const rule = screen.getByText("category matches niche").closest("li");
    expect(rule).toHaveTextContent("not checked");
    expect(rule).toHaveTextContent("–");
    expect(rule).not.toHaveTextContent("✓");
    expect(screen.getByText("has phone").closest("li")).toHaveTextContent("✓");
    expect(screen.getByText(/Scored with/)).toHaveTextContent(
      "Scored with general-b2b v1: For general outreach.",
    );
  });

  it("ticks a category it did check", () => {
    render(<ScoreReview review={review(true)} score={30} />);

    const rule = screen.getByText("category matches niche").closest("li");
    expect(rule).toHaveTextContent("✓");
    expect(rule).not.toHaveTextContent("not checked");
  });

  it("says what will score a lead that isn't scored yet", () => {
    render(<ScoreReview review={{ ...review(null), score_breakdown: [] }} score={null} />);

    expect(screen.getByText(/Will be scored with/)).toBeInTheDocument();
    expect(screen.getByText("Not scored yet.")).toBeInTheDocument();
  });
});
