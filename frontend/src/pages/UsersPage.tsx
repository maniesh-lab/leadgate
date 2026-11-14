/**
 * Users (admins): who can log in, adding a reviewer (`POST /api/users`), and turning an
 * account off or back on (`PATCH /api/users/{id}`). Turning an account off ends its
 * logins at once. Your own row has no buttons, and the API refuses to turn off the last
 * active admin; its message is shown as is.
 *
 * Recovery: "Reset link" makes a one-time link (30 minutes) the admin sends to the person
 * themselves; they pick their own password. "Unlock" lifts a login lock after too many
 * wrong passwords or codes. "Turn off 2FA" clears a lost phone's two-factor login.
 * For another admin, those two ask for your own password again (and a code, if your
 * two-factor is on) first: the API wants it, so a stolen login can't take over an admin.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { api, describeError } from "../api/client";
import type { AdminConfirm, Page, ResetLink, User, UserCreate, UserRow } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Badge } from "../components/Badge";
import { PlusIcon } from "../components/icons";
import { PageHeader } from "../components/Page";
import { Pagination } from "../components/Pagination";
import { ErrorMessage, Loading } from "../components/QueryState";
import { SettingsTabs } from "../components/SettingsTabs";
import { formatDateTime } from "../lib/format";

const PAGE_SIZE = 50;

export function UsersPage() {
  const { user: me } = useAuth();
  const [params, setParams] = useSearchParams();
  const page = Number(params.get("page") ?? 1);
  const [adding, setAdding] = useState(false);

  const users = useQuery({
    queryKey: ["users", { page }],
    queryFn: () => api.get<Page<UserRow>>("/api/users", { page, page_size: PAGE_SIZE }),
  });

  return (
    <section>
      <SettingsTabs />
      <PageHeader
        eyebrow="Settings · users"
        title={<span className="text-gradient">Users</span>}
        subtitle="Who can log in. Reviewers decide on leads; admins also run campaigns and change settings."
        actions={
          !adding && (
            <button type="button" className="btn-primary" onClick={() => setAdding(true)}>
              <PlusIcon /> Add reviewer
            </button>
          )
        }
      />

      {adding && <AddReviewer onDone={() => setAdding(false)} />}

      {users.isPending && <Loading />}
      {users.error && <ErrorMessage error={users.error} />}
      {users.data && (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Login</th>
                  <th>Added</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.data.items.map((user) => (
                  <tr key={user.id}>
                    <td className={user.is_active ? "font-medium" : "text-muted"}>
                      {user.email}
                      {user.id === me?.id && (
                        <span className="ml-1.5 text-xs text-faint">· you</span>
                      )}
                    </td>
                    <td className="font-mono text-xs text-muted">{user.role}</td>
                    <td>
                      <Badge tone={user.is_active ? "green" : "gray"}>
                        <span className="size-1.5 rounded-full bg-current" />
                        {user.is_active ? "active" : "off"}
                      </Badge>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        {user.two_factor_enabled && <Badge tone="cyan">2FA</Badge>}
                        {user.locked_until && (
                          <Badge tone="red">locked until {formatTime(user.locked_until)}</Badge>
                        )}
                      </div>
                    </td>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(user.created_at)}
                    </td>
                    <td className="text-right">
                      {user.id !== me?.id && <UserActions user={user} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={users.data.total}
            onPage={(next) => setParams(next > 1 ? { page: String(next) } : {})}
          />
        </>
      )}
    </section>
  );
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The buttons on someone else's row. */
function UserActions({ user }: { user: UserRow }) {
  return (
    <div className="flex flex-wrap justify-end gap-2">
      {user.is_active && <ResetLinkButton user={user} />}
      {user.locked_until && (
        <ActionButton user={user} path="unlock" label="Unlock" busyLabel="Unlocking…" />
      )}
      {user.two_factor_enabled && (
        <ActionButton
          user={user}
          path="two-factor/off"
          label="Turn off 2FA"
          busyLabel="Turning off…"
          needsConfirm={user.role === "admin"}
          confirm={`Turn off two-factor login for ${user.email}? Their logins end, and they can log in with just their password until they turn it on again.`}
        />
      )}
      <ActiveButton user={user} />
    </div>
  );
}

/** A one-time reset link, shown once with a copy button; the admin sends it themselves. */
function ResetLinkButton({ user }: { user: UserRow }) {
  const [copied, setCopied] = useState<boolean | null>(null); // null: not tried yet
  const [asking, setAsking] = useState(false);
  const button = useFocusBackOnCancel(asking);
  const make = useMutation({
    mutationFn: (confirm?: AdminConfirm) =>
      api.post<ResetLink>(`/api/users/${user.id}/reset-link`, confirm),
  });
  // The token goes after "#": browsers never send that part to a server.
  const link = make.data
    ? `${window.location.origin}/reset-password#token=${make.data.token}`
    : null;

  async function copy() {
    if (!link) return;
    try {
      // Missing (or refused) on a plain-http address: say so, rather than nothing.
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  if (!link) {
    return (
      <div>
        {asking ? (
          <ConfirmYourself
            label="Make link"
            busy={make.isPending}
            onConfirm={(confirm) => make.mutate(confirm)}
            onCancel={() => {
              button.cancelled();
              setAsking(false);
              make.reset();
            }}
          />
        ) : (
          <button
            ref={button.ref}
            type="button"
            className="btn px-3 py-1.5 whitespace-nowrap"
            disabled={make.isPending}
            onClick={() => (user.role === "admin" ? setAsking(true) : make.mutate(undefined))}
          >
            {make.isPending ? "Making…" : "Reset link"}
          </button>
        )}
        {make.error && (
          <p role="alert" className="mt-1 text-xs text-(--red)">
            {describeError(make.error)}
          </p>
        )}
      </div>
    );
  }
  return (
    <div className="w-72 text-left">
      <input
        readOnly
        aria-label={`Reset link for ${user.email}`}
        value={link}
        onFocus={(e) => e.target.select()}
        className="input font-mono text-xs"
      />
      <div className="mt-1 flex items-center gap-2 text-xs text-muted">
        <button
          type="button"
          className="btn px-2 py-1"
          aria-live="polite"
          onClick={() => void copy()}
        >
          {copied ? "Copied" : copied === false ? "Copy failed: select it and copy" : "Copy"}
        </button>
        <span>
          Works once, until {formatTime(make.data?.expires_at ?? "")}. Send it to them yourself.
        </span>
      </div>
    </div>
  );
}

/** A POST to `/api/users/{id}/<path>` that refreshes the list (asks first if `confirm`;
 * with `needsConfirm`, also for your own password again). */
function ActionButton(props: {
  user: UserRow;
  path: string;
  label: string;
  busyLabel: string;
  confirm?: string;
  needsConfirm?: boolean;
}) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const button = useFocusBackOnCancel(asking);
  const action = useMutation({
    mutationFn: (confirm?: AdminConfirm) =>
      api.post<UserRow>(`/api/users/${props.user.id}/${props.path}`, confirm),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  function run() {
    if (props.confirm && !window.confirm(props.confirm)) return;
    if (props.needsConfirm) setAsking(true);
    else action.mutate(undefined);
  }

  return (
    <div>
      {asking ? (
        <ConfirmYourself
          label={props.label}
          busy={action.isPending}
          onConfirm={(confirm) => action.mutate(confirm)}
          onCancel={() => {
            button.cancelled();
            setAsking(false);
            action.reset();
          }}
        />
      ) : (
        <button
          ref={button.ref}
          type="button"
          className="btn px-3 py-1.5 whitespace-nowrap"
          disabled={action.isPending}
          onClick={run}
        >
          {action.isPending ? props.busyLabel : props.label}
        </button>
      )}
      {action.error && (
        <p role="alert" className="mt-1 text-xs text-(--red)">
          {describeError(action.error)}
        </p>
      )}
    </div>
  );
}

/** Cancelling the password prompt puts keyboard focus back on the button it replaced. */
function useFocusBackOnCancel(asking: boolean) {
  const ref = useRef<HTMLButtonElement>(null);
  const wasCancelled = useRef(false);
  useEffect(() => {
    if (!asking && wasCancelled.current) {
      wasCancelled.current = false;
      ref.current?.focus();
    }
  }, [asking]);
  return {
    ref,
    cancelled: () => {
      wasCancelled.current = true;
    },
  };
}

/** Your own password again, and a code when your two-factor is on: the API asks for them
 * before a reset link or two-factor off for another admin, so a login token alone (one
 * stolen from this browser, say) can't take an admin over. */
function ConfirmYourself(props: {
  label: string;
  busy: boolean;
  onConfirm: (confirm: AdminConfirm) => void;
  onCancel: () => void;
}) {
  const { user: me } = useAuth();
  const needsCode = me?.two_factor_enabled ?? false;
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  // It replaces the button that opened it: keyboard focus goes to its first box.
  const passwordRef = useRef<HTMLInputElement>(null);
  useEffect(() => passwordRef.current?.focus(), []);

  function submit(event: FormEvent) {
    event.preventDefault();
    props.onConfirm({ password, code: needsCode ? code : null });
  }

  return (
    <form onSubmit={submit} className="w-56 space-y-1.5 text-left">
      <input
        ref={passwordRef}
        type="password"
        required
        maxLength={128}
        autoComplete="current-password"
        aria-label="Your password"
        placeholder="Your password"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        className="input py-1.5 text-xs"
      />
      {needsCode && (
        <input
          required
          inputMode="numeric"
          maxLength={16}
          autoComplete="one-time-code"
          aria-label="Your code"
          placeholder="Code from your app"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          className="input py-1.5 text-xs"
        />
      )}
      <div className="flex gap-2">
        <button type="submit" className="btn px-3 py-1.5 whitespace-nowrap" disabled={props.busy}>
          {props.busy ? "Checking…" : props.label}
        </button>
        <button type="button" className="btn px-3 py-1.5" onClick={props.onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** "Turn off" (asks first) or "Turn on" for one account. */
function ActiveButton({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const [asking, setAsking] = useState(false);
  const button = useFocusBackOnCancel(asking);
  const change = useMutation({
    mutationFn: (body: { is_active: boolean; confirm?: AdminConfirm }) =>
      api.patch<User>(`/api/users/${user.id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  function toggle() {
    if (user.is_active) {
      const sure = window.confirm(`Turn off ${user.email}? Their logins end right away.`);
      if (!sure) return;
      change.mutate({ is_active: false });
    } else if (user.role === "admin") {
      setAsking(true); // a former admin back on: the API wants your password again
    } else {
      change.mutate({ is_active: true });
    }
  }

  return (
    <div>
      {asking ? (
        <ConfirmYourself
          label="Turn on"
          busy={change.isPending}
          onConfirm={(confirm) => change.mutate({ is_active: true, confirm })}
          onCancel={() => {
            button.cancelled();
            setAsking(false);
            change.reset();
          }}
        />
      ) : (
        <button
          ref={button.ref}
          type="button"
          className="btn px-3 py-1.5 whitespace-nowrap"
          disabled={change.isPending}
          onClick={toggle}
        >
          {user.is_active ? "Turn off" : "Turn on"}
        </button>
      )}
      {change.error && (
        <p role="alert" className="mt-1 text-xs text-(--red)">
          {describeError(change.error)}
        </p>
      )}
    </div>
  );
}

/** The inline form that adds a reviewer (the API checks the email and the password). */
function AddReviewer({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const add = useMutation({
    mutationFn: (body: UserCreate) => api.post<User>("/api/users", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
      onDone();
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    add.mutate({ email, password, role: "reviewer" });
  }

  return (
    <form onSubmit={submit} className="card mb-6">
      <div className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <label className="block">
          <span className="eyebrow text-[10px]">Email</span>
          <input
            type="email"
            required
            autoComplete="off"
            placeholder="reviewer@agency.com"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="input mt-1.5"
          />
        </label>
        <label className="block">
          <span className="eyebrow text-[10px]">Password</span>
          <input
            type="password"
            required
            minLength={8}
            maxLength={128}
            autoComplete="new-password"
            placeholder="8 characters or more"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="input mt-1.5"
          />
        </label>
        <div className="flex gap-2">
          <button type="submit" className="btn-primary" disabled={add.isPending}>
            {add.isPending ? "Adding…" : "Add"}
          </button>
          <button type="button" className="btn" onClick={onDone}>
            Cancel
          </button>
        </div>
      </div>
      {add.error && (
        <p role="alert" className="mt-3 text-sm text-(--red)">
          {describeError(add.error)}
        </p>
      )}
    </form>
  );
}
