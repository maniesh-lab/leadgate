/**
 * Route guards. `RequireAuth` wraps every page but the login page: no user -> the login
 * page (which sends you back afterwards). `RequireRole` wraps admin-only pages, so a
 * reviewer who types the URL gets a plain "not for your role" message.
 *
 * These only hide things in the browser; the API checks the role on every request.
 */
import type { ReactNode } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { describeError } from "../api/client";
import type { UserRole } from "../api/types";
import { useAuth } from "./AuthProvider";

export function RequireAuth() {
  const { user, loading, error, logout } = useAuth();
  const location = useLocation();

  if (loading) return <p className="p-8 text-slate-500">Loading…</p>;
  if (error) {
    return (
      <div className="p-8">
        <p className="text-red-700">Couldn't load your account: {describeError(error)}</p>
        <button type="button" className="mt-4 underline" onClick={logout}>
          Log in again
        </button>
      </div>
    );
  }
  if (!user)
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <Outlet />;
}

export function RequireRole({ allowed, children }: { allowed: UserRole; children: ReactNode }) {
  const { user } = useAuth();
  if (user?.role !== allowed) {
    return <p className="text-slate-600">This page is for {allowed}s only.</p>;
  }
  return children;
}
