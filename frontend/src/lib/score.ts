/**
 * A score's colour: one step of the orange score ramp (`--score-1` … `--score-5` in
 * index.css) per 20 points, the same buckets as the dashboard's score chart. Low scores
 * are a quiet dark orange, high scores bright: hot leads look hot.
 */
export function scoreColor(score: number): string {
  const step = Math.min(5, Math.max(1, Math.floor(score / 20) + 1));
  return `var(--score-${step})`;
}
