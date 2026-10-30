/**
 * Why a lead scored what it did: each rubric rule, whether it matched, and its points,
 * then the LLM's short explanation (written from this breakdown only) and anything
 * extraction couldn't prove. Used on the run item and lead pages.
 */
import type { Review } from "../api/types";

// Rules can take points away too (points -100..100): show "+15" and "-10".
const signed = (points = 0) => (points > 0 ? `+${points}` : String(points));

type Rule = { rule?: string; field?: string; points?: number; hit?: boolean; required?: boolean };

export function ScoreReview({ review, score }: { review: Review; score: number | null }) {
  const rules = review.score_breakdown as Rule[];

  return (
    <div className="card space-y-3 text-sm">
      <h2 className="font-medium">Score: {score ?? "—"}</h2>
      {rules.length === 0 ? (
        <p className="text-slate-500">Not scored yet.</p>
      ) : (
        <table className="w-full text-left">
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.rule} className={rule.hit ? "" : "text-slate-400"}>
                <td className="w-6 py-0.5">{rule.hit ? "✓" : "✗"}</td>
                <td className="py-0.5">
                  {rule.rule}
                  {rule.required && <span className="ml-1 text-xs">(required)</span>}
                </td>
                <td className="py-0.5 text-right tabular-nums">
                  {rule.hit ? signed(rule.points) : `(${signed(rule.points)})`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {review.score_explanation && <p className="text-slate-700">{review.score_explanation}</p>}
      {review.extraction_issues.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-amber-800">What extraction couldn't prove</h3>
          <ul className="list-inside list-disc text-amber-800">
            {review.extraction_issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
