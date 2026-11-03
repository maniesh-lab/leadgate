/**
 * The switch between the admin's settings pages (Rubrics · Users), shown above each
 * one. The menu bar has a single "Settings" item, so it doesn't grow with every page.
 */
import { NavLink } from "react-router";

const TABS = [
  { to: "/settings/rubrics", label: "Rubrics" },
  { to: "/settings/users", label: "Users" },
];

export function SettingsTabs() {
  return (
    <nav aria-label="Settings" className="mb-6 flex gap-2">
      {TABS.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          className={({ isActive }) =>
            `rounded-full border px-3 py-1 text-sm transition-colors ${
              isActive ? "border-line-strong text-fg" : "border-line text-muted hover:text-fg"
            }`
          }
        >
          {tab.label}
        </NavLink>
      ))}
    </nav>
  );
}
