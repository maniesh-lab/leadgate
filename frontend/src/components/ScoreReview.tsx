/**
 * Why a lead scored what it did: the score in big type, each rubric rule with whether it
 * matched and its points, then the LLM's short explanation (written from this breakdown
 * only) and anything extraction couldn't prove. Used on the run item and lead pages
 * and in the approval queue's decision dialog.
 */
import type { Review } from "../api/types";

// Rules can take points away too (points -100..100): show "+15" and "-10".
const signed = (points = 0) => (points > 0 ? `+${points}` : String(points));

type Rule = { rule?: string; field?: string; points?: number; hit?: boolean; required?: boolean };

export function ScoreReview({ review, score }: { review: Review; score: number | null }) {
  const rules = review.score_breakdown as Rule[];

  return (
    <div className="card space-y-4">
      <div className="flex items-baseline justify-between">
        <span className="eyebrow">Score</span>
        <span>
          <span className="tone-orange text-gradient text-5xl leading-none">{score ?? "—"}</span>
          <span className="ml-1 text-sm text-faint">/ 100</span>
        </span>
      </div>
      {rules.length === 0 ? (
        <p className="text-sm text-muted">Not scored yet.</p>
      ) : (
        <ul className="space-y-1.5 text-sm">
          {rules.map((rule) => (
            <li key={rule.rule} className="flex items-center gap-3">
              <span
                className={`flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                  rule.hit ? "bg-(--green-bg) text-(--green)" : "bg-surface-2 text-faint"
                }`}
              >
                {rule.hit ? "✓" : "✗"}
              </span>
              <span className={rule.hit ? "font-medium" : "text-faint"}>
                {rule.rule}
                {rule.required && <span className="eyebrow ml-2 text-[9px]">required</span>}
              </span>
              <span
                className={`ml-auto font-mono text-xs tabular-nums ${rule.hit ? "font-semibold text-(--green)" : "text-faint"}`}
              >
                {rule.hit ? signed(rule.points) : `(${signed(rule.points)})`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {review.score_explanation && (
        <p className="border-t border-line pt-4 text-sm text-muted">{review.score_explanation}</p>
      )}
      {review.extraction_issues.length > 0 && (
        <div className="rounded-xl bg-(--amber-bg) p-3 text-sm text-(--amber)">
          <p className="font-semibold">What extraction couldn't prove</p>
          <ul className="mt-1 list-inside list-disc">
            {review.extraction_issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
