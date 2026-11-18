/**
 * Who sees the admin tools, and how:
 *
 * - "full": an admin. Every admin page and button works.
 * - "locked": the public demo's login (a reviewer every visitor shares, `lib/demo`). The
 *   demo should look like the real app, so it sees every admin page and button too, but
 *   anything that would change data is turned off (`components/Locked`). The API refuses
 *   those for a reviewer anyway; this only decides what the dashboard shows.
 * - "none": any other reviewer. The admin tools stay hidden.
 */
import type { User } from "../api/types";
import { type DemoLogin, demoLogin } from "../lib/demo";
import { useAuth } from "./AuthProvider";

export type AdminAccess = "full" | "locked" | "none";

export function adminAccess(
  user: Pick<User, "email" | "role"> | null,
  demo: DemoLogin | null = demoLogin(),
): AdminAccess {
  if (user?.role === "admin") return "full";
  if (user && demo && user.email.toLowerCase() === demo.email.toLowerCase()) return "locked";
  return "none";
}

export function useAdminAccess(): AdminAccess {
  const { user } = useAuth();
  return adminAccess(user);
}
