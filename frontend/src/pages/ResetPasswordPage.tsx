/**
 * The page a one-time reset link opens (`/reset-password#token=...`): pick a new password.
 * An admin made the link (Users page) or the `reset-password` command printed it. The
 * token sits after "#", so the browser never sends it to a server or in a Referer header;
 * the page reads it once and takes it out of the address bar. The API checks it works
 * once and hasn't expired, then ends every older login of that user.
 */
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router";
import { api, describeError } from "../api/client";
import { ThemeToggle, Wordmark } from "../components/Brand";

/** The token from `#token=...`. */
function readToken(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("token");
}

export function ResetPasswordPage() {
  const [token] = useState(readToken);
  // Once read, take it out of the address bar (and this history entry).
  useEffect(() => {
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
  }, []);
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const reset = useMutation({
    mutationFn: () => api.post<void>("/api/auth/reset-password", { token, new_password: password }),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== repeat) {
      setProblem("The two passwords don't match.");
      return;
    }
    setProblem(null);
    reset.mutate();
  }

  return (
    <main className="flex min-h-screen flex-col px-4 py-6">
      <div className="mx-auto flex w-full max-w-md items-center justify-between">
        <Wordmark />
        <ThemeToggle />
      </div>
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12">
        <p className="eyebrow mb-4">Reset password</p>
        <h1 className="text-4xl leading-tight font-semibold tracking-tight">
          Pick a new <span className="text-gradient">password</span>.
        </h1>

        {!token ? (
          <p role="alert" className="card mt-8 text-sm">
            This link is incomplete. Ask an admin for a new one.
          </p>
        ) : reset.isSuccess ? (
          <div role="status" className="card mt-8 space-y-3 text-sm">
            <p>Your password is set. Your other logins were ended.</p>
            <Link to="/login" className="btn-primary w-full py-2.5">
              Log in
            </Link>
          </div>
        ) : (
          <form onSubmit={submit} className="card mt-8 space-y-4">
            <p className="text-xs text-muted">At least 8 characters. The link works once.</p>
            <label className="block">
              <span className="eyebrow text-[10px]">New password</span>
              <input
                type="password"
                required
                minLength={8}
                maxLength={128}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input mt-1.5"
              />
            </label>
            <label className="block">
              <span className="eyebrow text-[10px]">Repeat new password</span>
              <input
                type="password"
                required
                minLength={8}
                maxLength={128}
                autoComplete="new-password"
                value={repeat}
                onChange={(e) => setRepeat(e.target.value)}
                className="input mt-1.5"
              />
            </label>
            {(problem || reset.error) && (
              <p role="alert" className="text-sm text-(--red)">
                {problem ?? describeError(reset.error)}
              </p>
            )}
            <button type="submit" disabled={reset.isPending} className="btn-primary w-full py-2.5">
              {reset.isPending ? "Saving…" : "Set password"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
