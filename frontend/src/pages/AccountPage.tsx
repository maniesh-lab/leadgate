/**
 * Your account: who you are, and changing your password (`POST /api/auth/password`).
 * The API checks the current password, then ends every older login token, so other
 * browsers (or anyone holding an old token) are logged out; this browser gets a fresh
 * token back and stays logged in.
 */
import { useMutation } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { api, describeError } from "../api/client";
import type { PasswordChange, Token } from "../api/types";
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

        <form onSubmit={submit} className="card max-w-xl space-y-4">
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
      </div>
    </section>
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
