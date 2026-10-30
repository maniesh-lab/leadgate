/** A small "label above value" pair, for the fact grids on the detail pages. */
import type { ReactNode } from "react";

export function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="break-words">
        {value === null || value === undefined || value === "" ? "—" : value}
      </div>
    </div>
  );
}
