/**
 * Users (admins): who can log in, adding a reviewer (`POST /api/users`), and turning an
 * account off or back on (`PATCH /api/users/{id}`). Turning an account off ends its
 * logins at once. Your own row has no button, and the API refuses to turn off the last
 * active admin; its message is shown as is.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router";
import { api, describeError } from "../api/client";
import type { Page, User, UserCreate } from "../api/types";
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
    queryFn: () => api.get<Page<User>>("/api/users", { page, page_size: PAGE_SIZE }),
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
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(user.created_at)}
                    </td>
                    <td className="text-right">
                      {user.id !== me?.id && <ActiveButton user={user} />}
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

/** "Turn off" (asks first) or "Turn on" for one account. */
function ActiveButton({ user }: { user: User }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (isActive: boolean) =>
      api.patch<User>(`/api/users/${user.id}`, { is_active: isActive }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });

  function toggle() {
    if (user.is_active) {
      const sure = window.confirm(`Turn off ${user.email}? Their logins end right away.`);
      if (!sure) return;
    }
    change.mutate(!user.is_active);
  }

  return (
    <div>
      <button
        type="button"
        className="btn px-3 py-1.5 whitespace-nowrap"
        disabled={change.isPending}
        onClick={toggle}
      >
        {user.is_active ? "Turn off" : "Turn on"}
      </button>
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
