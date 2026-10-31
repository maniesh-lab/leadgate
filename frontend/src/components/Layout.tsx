/**
 * The frame around every logged-in page: a floating pill bar with the mark, the menu
 * (the active item shows a dot in its page's tone), the admin's "New campaign" button, the theme switch, the account link and log out,
 * then the page itself (`<Outlet />`) and the footer. Reviewers don't get the
 * admin-only button or "Settings".
 */
import { Link, NavLink, Outlet } from "react-router";
import { useAuth } from "../auth/AuthProvider";
import { ThemeToggle, Wordmark } from "./Brand";
import { Footer } from "./Footer";
import { LogOutIcon, PlusIcon, UserIcon } from "./icons";
import { Dot } from "./Page";

// Every logged-in user sees these; admins also get "Settings" (ADMIN_NAV). `dot` is the
// page's tone (PageHeader's `tone`), shown as a dot on the active item.
const NAV = [
  { to: "/dashboard", label: "Dashboard", dot: "var(--accent)" },
  { to: "/queue", label: "Queue", dot: "var(--tone-amber)" },
  { to: "/campaigns", label: "Campaigns", dot: "var(--tone-blue)" },
  { to: "/runs", label: "Runs", dot: "var(--tone-green)" },
  { to: "/leads", label: "Leads", dot: "var(--tone-orange)" },
  { to: "/audit", label: "Audit", dot: "var(--accent)" },
];
const ADMIN_NAV = [{ to: "/settings/rubrics", label: "Settings", dot: "var(--tone-orange)" }];

export function Layout() {
  const { user, logout } = useAuth();
  const nav = user?.role === "admin" ? [...NAV, ...ADMIN_NAV] : NAV;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 px-4 pt-4">
        <nav
          aria-label="Main"
          className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 rounded-3xl border border-line bg-surface/75 px-4 py-2.5 backdrop-blur-md sm:rounded-full"
        >
          <Link to="/dashboard" aria-label="leadgate home">
            <Wordmark />
          </Link>

          <div className="order-last -mx-1 flex w-full gap-1 overflow-x-auto sm:order-none sm:mx-auto sm:w-auto">
            {nav.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm whitespace-nowrap transition-colors ${
                    isActive
                      ? "border-line-strong bg-surface-2 text-fg"
                      : "border-transparent text-muted hover:text-fg"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive && <Dot color={item.dot} />}
                    {item.label}
                  </>
                )}
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
            <div className="hidden text-right leading-tight 2xl:block">
              <div className="text-xs font-medium">{user?.email}</div>
              <div className="eyebrow text-[10px]">{user?.role}</div>
            </div>
            <Link
              to="/account"
              aria-label="Account"
              title="Account"
              className="flex size-8 items-center justify-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-fg"
            >
              <UserIcon />
            </Link>
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
      <Footer />
    </div>
  );
}
