/**
 * Your account: who you are, changing your password (`POST /api/auth/password`), and
 * two-factor login (`/api/auth/two-factor/*`). The API checks the current password, then
 * ends every older login token, so other browsers (or anyone holding an old token) are
 * logged out; this browser gets a fresh token back and stays logged in. Turning two-factor
 * on or off does the same.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api, describeError } from "../api/client";
import type { PasswordChange, Token, TwoFactorSetup } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Fact, PageHeader } from "../components/Page";

export function AccountPage() {
  const { user, swapToken } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  const change = useMutation({
    mutationFn: (body: PasswordChange) => api.post<Token>("/api/auth/password", body),
    onSuccess: (token) => {
      swapToken(token.access_token);
      setCurrent("");
      setNext("");
      setRepeat("");
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (next !== repeat) {
      setProblem("The two new passwords don't match.");
      return;
    }
    setProblem(null);
    change.mutate({ current_password: current, new_password: next });
  }

  return (
    <section>
      <PageHeader
        eyebrow="Account"
        title={
          <>
            Your <span className="text-gradient">account</span>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[1fr_2fr]">
        <div className="card grid content-start gap-5">
          <Fact label="Email" value={user?.email} />
          <Fact label="Role" value={user?.role} />
        </div>

        <form onSubmit={submit} aria-label="Change password" className="card max-w-xl space-y-4">
          <div>
            <h2 className="font-semibold">Change password</h2>
            <p className="text-xs text-muted">
              At least 8 characters. Your other browsers will be logged out.
            </p>
          </div>
          <PasswordField label="Current password" value={current} onChange={setCurrent} current />
          <PasswordField label="New password" value={next} onChange={setNext} />
          <PasswordField label="Repeat new password" value={repeat} onChange={setRepeat} />

          {(problem || change.error) && (
            <p role="alert" className="text-sm text-(--red)">
              {problem ?? describeError(change.error)}
            </p>
          )}
          {change.isSuccess && !problem && (
            <p role="status" className="text-sm text-(--green)">
              Password changed. Your other browsers were logged out.
            </p>
          )}

          <button type="submit" className="btn-primary" disabled={change.isPending}>
            {change.isPending ? "Saving…" : "Change password"}
          </button>
        </form>
        <div className="lg:col-start-2">
          <TwoFactorCard />
        </div>
      </div>
    </section>
  );
}

/** Two-factor login: on (turn it off with password + code) or off (set it up). */
function TwoFactorCard() {
  const { user, swapToken } = useAuth();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);

  // A new token comes back (the other logins end); then reload who the user is.
  function done(token: Token) {
    swapToken(token.access_token);
    void queryClient.invalidateQueries({ queryKey: ["me"] });
    setPassword("");
    setCode("");
    setSetup(null);
  }

  const start = useMutation({
    mutationFn: () => api.post<TwoFactorSetup>("/api/auth/two-factor/setup", { password }),
    onSuccess: (answer) => {
      setSetup(answer);
      setPassword("");
    },
  });
  const enable = useMutation({
    mutationFn: () => api.post<Token>("/api/auth/two-factor/enable", { code }),
    onSuccess: done,
  });
  const disable = useMutation({
    mutationFn: () => api.post<Token>("/api/auth/two-factor/disable", { password, code }),
    onSuccess: done,
  });
  const error = start.error ?? enable.error ?? disable.error;

  function submit(event: FormEvent) {
    event.preventDefault();
    // Only this step's outcome shows: an earlier "it's on" mustn't stay after turning off.
    if (user?.two_factor_enabled) {
      enable.reset();
      disable.mutate();
    } else if (setup) {
      disable.reset();
      enable.mutate();
    } else start.mutate();
  }

  return (
    <form onSubmit={submit} aria-label="Two-factor login" className="card max-w-xl space-y-4">
      <div>
        <h2 className="font-semibold">Two-factor login</h2>
        <p className="text-xs text-muted">
          {user?.two_factor_enabled
            ? "On: logging in also needs a code from your authenticator app."
            : "Off. With it on, logging in also needs a 6-digit code from an authenticator app (such as Google Authenticator, Authy or 1Password)."}
        </p>
      </div>

      {user?.two_factor_enabled ? (
        <>
          <PasswordField label="Current password" value={password} onChange={setPassword} current />
          <CodeField value={code} onChange={setCode} />
          <button type="submit" className="btn" disabled={disable.isPending}>
            {disable.isPending ? "Turning off…" : "Turn off two-factor"}
          </button>
        </>
      ) : setup ? (
        <>
          <p className="text-sm">
            Scan this with your authenticator app, then type the code it shows.
          </p>
          <img
            src={setup.qr_svg}
            alt="QR code for your authenticator app"
            className="size-48 rounded bg-white p-2"
          />
          <p className="text-xs text-muted">
            Can't scan? Type this key instead:{" "}
            <code className="font-mono break-all text-fg">{setup.secret}</code>
          </p>
          <CodeField value={code} onChange={setCode} />
          <button type="submit" className="btn-primary" disabled={enable.isPending}>
            {enable.isPending ? "Checking…" : "Turn on"}
          </button>
        </>
      ) : (
        <>
          <PasswordField label="Current password" value={password} onChange={setPassword} current />
          <button type="submit" className="btn-primary" disabled={start.isPending}>
            {start.isPending ? "Starting…" : "Set up two-factor"}
          </button>
        </>
      )}

      {error && (
        <p role="alert" className="text-sm text-(--red)">
          {describeError(error)}
        </p>
      )}
      {(enable.isSuccess || disable.isSuccess) && (
        <p role="status" className="text-sm text-(--green)">
          {enable.isSuccess ? "Two-factor login is on." : "Two-factor login is off."} Your other
          browsers were logged out.
        </p>
      )}
    </form>
  );
}

function CodeField(props: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="block">
      <span className="eyebrow text-[10px]">Code from the app</span>
      <input
        required
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9 ]{6,7}"
        maxLength={7}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="input mt-1.5 font-mono tracking-widest"
      />
    </label>
  );
}

function PasswordField(props: {
  label: string;
  value: string;
  current?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="eyebrow text-[10px]">{props.label}</span>
      <input
        type="password"
        required
        minLength={props.current ? undefined : 8}
        maxLength={128}
        autoComplete={props.current ? "current-password" : "new-password"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="input mt-1.5"
      />
    </label>
  );
}
