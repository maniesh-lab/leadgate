/** The login form: email + password -> a token, then back to the page you came from. */
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { describeError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { ThemeToggle, Wordmark } from "../components/Brand";
import { ArrowRightIcon } from "../components/icons";

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Where RequireAuth sent us from (e.g. /leads?status=new); the start page otherwise.
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col px-4 py-6">
      <div className="mx-auto flex w-full max-w-md items-center justify-between">
        <Wordmark />
        <ThemeToggle />
      </div>

      <div className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12">
        <p className="eyebrow mb-4">Sign in</p>
        <h1 className="text-5xl leading-tight font-semibold tracking-tight">
          Welcome <span className="text-gradient">back</span>.
        </h1>
        <p className="mt-3 text-muted">
          Agents find and score the leads. You decide what goes out.
        </p>

        <form onSubmit={submit} className="card mt-8 space-y-4">
          <label className="block">
            <span className="eyebrow text-[10px]">Email</span>
            <input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input mt-1.5"
            />
          </label>
          <label className="block">
            <span className="eyebrow text-[10px]">Password</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input mt-1.5"
            />
          </label>
          {error && (
            <p role="alert" className="text-sm text-(--red)">
              {error}
            </p>
          )}
          <button type="submit" disabled={busy} className="btn-primary w-full py-2.5">
            {busy ? "Logging in…" : "Log in"}
            {!busy && <ArrowRightIcon />}
          </button>
        </form>
      </div>
    </main>
  );
}
