/**
 * The login form: email + password -> a token, then back to the page you came from. With
 * two-factor login on, a second step asks for the code from the authenticator app.
 * On the public demo it also shows the demo reviewer's login, one click away.
 */
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { describeError } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { ThemeToggle, Wordmark } from "../components/Brand";
import { ArrowRightIcon } from "../components/icons";
import { demoLogin } from "../lib/demo";

export function LoginPage() {
  const { user, login, completeTwoFactor } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Set once the password was right and a two-factor code is needed.
  const [twoFactorToken, setTwoFactorToken] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const demo = demoLogin();

  // Where RequireAuth sent us from (e.g. /leads?status=new); the start page otherwise.
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (user) return <Navigate to={from} replace />;

  async function logIn(asEmail: string, asPassword: string) {
    setBusy(true);
    setError(null);
    try {
      const step = await login(asEmail, asPassword);
      if (step) {
        setTwoFactorToken(step.twoFactorToken);
        setPassword("");
        return;
      }
      navigate(from, { replace: true });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void logIn(email, password);
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault();
    if (!twoFactorToken) return;
    setBusy(true);
    setError(null);
    try {
      await completeTwoFactor(twoFactorToken, code);
      navigate(from, { replace: true });
    } catch (err) {
      setError(describeError(err));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  function startOver() {
    setTwoFactorToken(null);
    setCode("");
    setError(null);
  }

  function logInAsDemo() {
    if (!demo) return;
    setEmail(demo.email);
    setPassword(demo.password);
    void logIn(demo.email, demo.password);
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

        {twoFactorToken ? (
          <form onSubmit={submitCode} className="card mt-8 space-y-4">
            <p className="text-sm text-muted">
              Enter the 6-digit code from your authenticator app for {email}.
            </p>
            <label className="block">
              <span className="eyebrow text-[10px]">Code</span>
              <input
                required
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9 ]{6,7}"
                maxLength={7}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                className="input mt-1.5 font-mono tracking-widest"
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-(--red)">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy} className="btn-primary w-full py-2.5">
              {busy ? "Checking…" : "Log in"}
            </button>
            <button type="button" onClick={startOver} className="btn w-full py-2">
              Start over
            </button>
          </form>
        ) : (
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
        )}

        {demo && !twoFactorToken && (
          <section aria-label="Demo login" className="card mt-4">
            <p className="eyebrow text-[10px]">Just looking?</p>
            <p className="mt-2 text-sm text-muted">
              Log in as the demo reviewer and approve or reject a few made-up leads:
            </p>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-sm">
              <dt className="text-muted">email</dt>
              <dd className="break-all">{demo.email}</dd>
              <dt className="text-muted">password</dt>
              <dd className="break-all">{demo.password}</dd>
            </dl>
            <button
              type="button"
              disabled={busy}
              onClick={logInAsDemo}
              className="btn mt-4 w-full py-2.5"
            >
              Use the demo login
            </button>
          </section>
        )}
      </div>
    </main>
  );
}
