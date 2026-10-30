/**
 * The frame around every logged-in page: the top bar with the menu and the user, and the
 * page itself below (`<Outlet />`). Menu entries marked `adminOnly` are hidden from
 * reviewers.
 */
import { NavLink, Outlet } from "react-router";
import { useAuth } from "../auth/AuthProvider";

type NavItem = { to: string; label: string; adminOnly?: boolean };

const NAV: NavItem[] = [
  { to: "/campaigns", label: "Campaigns" },
  { to: "/campaigns/new", label: "New campaign", adminOnly: true },
  { to: "/runs", label: "Runs" },
  { to: "/leads", label: "Leads" },
];

export function Layout() {
  const { user, logout } = useAuth();
  const items = NAV.filter((item) => !item.adminOnly || user?.role === "admin");

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="font-semibold">leadgate</span>
          <nav className="flex flex-wrap gap-4 text-sm">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end
                className={({ isActive }) =>
                  isActive ? "font-medium text-slate-900" : "text-slate-500 hover:text-slate-900"
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-slate-500">
              {user?.email} · {user?.role}
            </span>
            <button type="button" onClick={logout} className="underline">
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
