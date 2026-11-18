/**
 * The public demo's locked admin tools (see `auth/access`). A locked button looks turned
 * off but can still be pressed, and then says why: a tooltip alone never shows on a
 * phone. It never sends anything. `LockedNotice` sits at the top of an admin page.
 */
import { type ReactNode, useState } from "react";

export const LOCKED_NOTE = "Admins only. Turned off on the public demo.";

export function LockedButton({
  children,
  className = "btn px-3 py-1.5 whitespace-nowrap",
}: {
  children: ReactNode;
  className?: string;
}) {
  const [told, setTold] = useState(false);
  return (
    <span className="inline-flex flex-col items-start">
      <button
        type="button"
        aria-disabled="true"
        title={LOCKED_NOTE}
        className={`${className} cursor-not-allowed opacity-50`}
        onClick={() => setTold(true)}
      >
        {children}
      </button>
      {told && (
        <span role="status" className="mt-1 text-xs text-muted">
          {LOCKED_NOTE}
        </span>
      )}
    </span>
  );
}

export function LockedNotice({ children }: { children?: ReactNode }) {
  return (
    <p role="note" className="card mb-6 text-sm text-muted">
      {children ??
        "This is an admin page, shown as it is in the real app. On the public demo you can look and try things out, but saving, uploading and deleting are turned off."}
    </p>
  );
}
