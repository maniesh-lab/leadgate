/**
 * The frame around every logged-in page: a floating pill bar with the mark, the menu,
 * the admin's "New campaign" button, the theme switch and the user, then the page
 * itself (`<Outlet />`). Reviewers don't get the admin-only button.
 */
import { Link, NavLink, Outlet } from "react-router";
import { useAuth } from "../auth/AuthProvider";
import { ThemeToggle, Wordmark } from "./Brand";
import { LogOutIcon, PlusIcon } from "./icons";

const NAV = [
  { to: "/queue", label: "Queue" },
  { to: "/campaigns", label: "Campaigns" },
  { to: "/runs", label: "Runs" },
  { to: "/leads", label: "Leads" },
];

export function Layout() {
  const { user, logout } = useAuth();

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 px-4 pt-4">
        <nav
          aria-label="Main"
          className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 rounded-3xl border border-line bg-surface/75 px-4 py-2.5 backdrop-blur-md sm:rounded-full"
        >
          <Link to="/campaigns" aria-label="leadgate home">
            <Wordmark />
          </Link>

          <div className="order-last -mx-1 flex w-full gap-1 overflow-x-auto sm:order-none sm:mx-auto sm:w-auto">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-full border px-4 py-1.5 text-sm transition-colors ${
                    isActive
                      ? "border-line-strong bg-surface-2 text-fg"
                      : "border-transparent text-muted hover:text-fg"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </div>

          <div className="ml-auto flex items-center gap-2 sm:ml-0">
            {user?.role === "admin" && (
              <Link
                to="/campaigns/new"
                aria-label="New campaign"
                className="btn-primary px-3 py-1.5"
              >
                <PlusIcon />
                <span className="hidden md:inline">New campaign</span>
              </Link>
            )}
            <ThemeToggle />
            <div className="hidden text-right leading-tight lg:block">
              <div className="text-xs font-medium">{user?.email}</div>
              <div className="eyebrow text-[10px]">{user?.role}</div>
            </div>
            <button
              type="button"
              onClick={logout}
              aria-label="Log out"
              title="Log out"
              className="flex size-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-fg"
            >
              <LogOutIcon />
            </button>
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 pt-10 pb-16">
        <Outlet />
      </main>
    </div>
  );
}
