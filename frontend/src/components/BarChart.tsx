/**
 * A small one-series bar chart for the dashboard, in plain HTML (no chart library).
 * Bars grow from the baseline in the chart's topic colour (`color`, a CSS colour such as
 * "var(--collected)"; a bar's own `color` wins, e.g. the score ramp), with 2px gaps and
 * rounded tops;
 * hovering or focusing a bar shows its label and detail in the line above, so every
 * value is readable without colour. Each bar is a button with an aria-label, so
 * keyboards and screen readers reach it too.
 */
import { useState } from "react";

export type Bar = { label: string; value: number; detail: string; color?: string };

type Props = {
  bars: Bar[];
  /** Shown above the chart when no bar is hovered, e.g. "312 collected in 30 days". */
  summary: string;
  /** Label every bar (few bars), or only the first and last (one bar per day). */
  labelAll?: boolean;
  /** The bars' colour (a CSS colour); the accent when not given. */
  color?: string;
};

export function BarChart({ bars, summary, labelAll = false, color = "var(--accent)" }: Props) {
  const [hovered, setHovered] = useState<number | null>(null);
  const max = Math.max(1, ...bars.map((bar) => bar.value));
  const active = hovered === null ? null : bars[hovered];

  return (
    <div>
      <p className="mb-3 h-5 truncate font-mono text-xs text-muted" aria-live="polite">
        {active ? (
          <>
            <span className="text-fg">{active.label}</span> · {active.detail}
          </>
        ) : (
          summary
        )}
      </p>
      <div className="flex h-36 items-end gap-0.5 border-b border-line">
        {bars.map((bar, index) => (
          <button
            type="button"
            key={bar.label}
            aria-label={`${bar.label}: ${bar.detail}`}
            onMouseEnter={() => setHovered(index)}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(index)}
            onBlur={() => setHovered(null)}
            className="flex h-full flex-1 cursor-default items-end outline-none focus-visible:bg-surface-2"
          >
            <span
              className={`block w-full rounded-t-[4px] transition-opacity ${
                hovered !== null && hovered !== index ? "opacity-40" : ""
              }`}
              style={{
                height: bar.value === 0 ? 0 : `${Math.max((bar.value / max) * 100, 2)}%`,
                background: bar.color ?? color,
              }}
            />
          </button>
        ))}
      </div>
      <div className="mt-1.5 flex gap-0.5 font-mono text-[10px] text-faint">
        {labelAll ? (
          bars.map((bar) => (
            <span key={bar.label} className="flex-1 truncate text-center">
              {bar.label}
            </span>
          ))
        ) : (
          <>
            <span>{bars[0]?.label}</span>
            <span className="ml-auto">{bars.at(-1)?.label}</span>
          </>
        )}
      </div>
    </div>
  );
}
