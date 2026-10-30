/**
 * Rubric settings (admins only): the scoring rubrics and their versions, and an editor
 * for the rules. Saving never changes a version: it adds the next one
 * (`POST /api/rubrics/{id}`), so campaigns and past scores keep the rules they used;
 * pick the new version on a campaign to use it. "New rubric" starts version 1
 * (`POST /api/rubrics`). The API checks every rule and its 422 is shown as is.
 *
 * The open version is in the URL (`/settings/rubrics?id=5`, or `?id=new`).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api, describeError } from "../api/client";
import { useRubrics } from "../api/hooks";
import type { Rubric, Rule } from "../api/types";
import { PlusIcon } from "../components/icons";
import { EmptyState, PageHeader } from "../components/Page";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../lib/format";

type Op = Rule["op"];

const OPS: { op: Op; help: string }[] = [
  { op: "exists", help: "has a value" },
  { op: "missing", help: "has no value" },
  { op: "eq", help: "equals" },
  { op: "gte", help: "at least" },
  { op: "lte", help: "at most" },
  { op: "in", help: "one of (comma-separated)" },
  { op: "contains", help: "contains" },
  { op: "older_than_years", help: "a year more than N years ago" },
];
const NO_VALUE: Op[] = ["exists", "missing"];
const NUMBER_OPS: Op[] = ["gte", "lte", "older_than_years"];

// Facts the score node gives the rubric engine (suggestions; the API takes any name).
const FIELDS = [
  "phone",
  "email",
  "website",
  "rating",
  "review_count",
  "business_status",
  "category",
  "category_matches_niche",
  "established_year",
  "signals.https",
  "signals.mobile_viewport",
  "signals.has_form",
  "signals.copyright_year",
  "signals.https_and_mobile",
];

/** A rule while it's edited: every box holds text until it's saved. */
type Draft = {
  key: number;
  label: string;
  field: string;
  op: Op;
  value: string;
  points: string;
  required: boolean;
};

let nextKey = 1;

function toDraft(rule: Partial<Rule>): Draft {
  const value = rule.value;
  return {
    key: nextKey++,
    label: rule.label ?? "",
    field: rule.field ?? "",
    op: rule.op ?? "exists",
    value: Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value),
    points: rule.points === undefined ? "" : String(rule.points),
    required: rule.required ?? false,
  };
}

/** The text in the value box -> what the API stores: a number, a list, true/false or text. */
function parseValue(op: Op, text: string): unknown {
  const trimmed = text.trim();
  if (NO_VALUE.includes(op)) return null;
  if (NUMBER_OPS.includes(op)) return trimmed === "" ? null : Number(trimmed);
  if (op === "in")
    return trimmed
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed !== "" && !Number.isNaN(Number(trimmed))) return Number(trimmed);
  return trimmed === "" ? null : trimmed;
}

function toRule(draft: Draft): Rule {
  return {
    label: draft.label.trim(),
    field: draft.field.trim(),
    op: draft.op,
    value: parseValue(draft.op, draft.value),
    points: Number(draft.points || 0),
    required: draft.required,
  };
}

export function RubricsPage() {
  const rubrics = useRubrics();
  const [params, setParams] = useSearchParams();
  const openId = params.get("id");

  if (rubrics.isPending) return <Loading />;
  if (rubrics.error) return <ErrorMessage error={rubrics.error} />;

  // The API lists them by name, newest version first: group them by name.
  const byName = new Map<string, Rubric[]>();
  for (const rubric of rubrics.data) {
    byName.set(rubric.name, [...(byName.get(rubric.name) ?? []), rubric]);
  }
  const open =
    openId === "new"
      ? null
      : (rubrics.data.find((r) => String(r.id) === openId) ?? rubrics.data[0]);

  return (
    <section>
      <PageHeader
        eyebrow="Settings"
        title={
          <>
            Scoring <span className="text-gradient">rubrics</span>
          </>
        }
        subtitle="The rules that score each lead from 0 to 100. Saving adds a new version; campaigns keep the version they picked, so past scores never change."
        actions={
          <button type="button" className="btn" onClick={() => setParams({ id: "new" })}>
            <PlusIcon /> New rubric
          </button>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
        <nav aria-label="Rubrics" className="space-y-4">
          {[...byName.entries()].map(([name, versions]) => (
            <div key={name}>
              <p className="mb-1.5 font-medium">{name}</p>
              <div className="flex flex-wrap gap-1.5">
                {versions.map((rubric) => (
                  <button
                    key={rubric.id}
                    type="button"
                    onClick={() => setParams({ id: String(rubric.id) })}
                    className={`rounded-full border px-2.5 py-0.5 font-mono text-xs transition-colors ${
                      rubric.id === open?.id && openId !== "new"
                        ? "border-line-strong bg-surface-2 text-fg"
                        : "border-line text-muted hover:text-fg"
                    }`}
                  >
                    v{rubric.version}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {openId === "new" ? (
          <RubricEditor
            key="new"
            rubric={null}
            latest={0}
            onSaved={(id) => setParams({ id: String(id) })}
          />
        ) : open ? (
          <RubricEditor
            key={open.id}
            rubric={open}
            latest={byName.get(open.name)?.[0]?.version ?? open.version}
            onSaved={(id) => setParams({ id: String(id) })}
          />
        ) : (
          <EmptyState title="No rubrics yet." hint="Run `python -m app.cli seed`, or add one." />
        )}
      </div>
    </section>
  );
}

type EditorProps = {
  rubric: Rubric | null; // null = a new rubric
  latest: number;
  onSaved: (id: number) => void;
};

/** The page gives each version its own `key`, so opening another one starts fresh. */
function RubricEditor({ rubric, latest, onSaved }: EditorProps) {
  const queryClient = useQueryClient();
  const start = () => (rubric ? (rubric.rules as Partial<Rule>[]).map(toDraft) : [toDraft({})]);
  const [name, setName] = useState("");
  const [drafts, setDrafts] = useState<Draft[]>(start);

  const save = useMutation({
    mutationFn: () => {
      const rules = drafts.map(toRule);
      return rubric
        ? api.post<Rubric>(`/api/rubrics/${rubric.id}`, { rules })
        : api.post<Rubric>("/api/rubrics", { name: name.trim(), rules });
    },
    onSuccess: (saved) => {
      queryClient.invalidateQueries({ queryKey: ["rubrics"] });
      onSaved(saved.id);
    },
  });

  function change(key: number, patch: Partial<Draft>) {
    setDrafts((current) => current.map((d) => (d.key === key ? { ...d, ...patch } : d)));
  }

  const maxPoints = drafts.reduce((sum, d) => sum + Math.max(0, Number(d.points) || 0), 0);

  return (
    <form
      className="card space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        {rubric ? (
          <div>
            <h2 className="text-xl font-semibold">
              {rubric.name}{" "}
              <span className="font-mono text-base text-muted">v{rubric.version}</span>
            </h2>
            <p className="text-xs text-muted">
              Saved {formatDateTime(rubric.created_at)}
              {rubric.version < latest && ` · an older version (latest is v${latest})`}
            </p>
          </div>
        ) : (
          <label className="block w-64">
            <span className="eyebrow mb-1 block text-[10px]">Name</span>
            <input
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. plumber-leads"
              pattern="[a-z0-9\-]{1,50}"
              title="lowercase letters, digits and dashes"
              required
            />
          </label>
        )}
        <p className="font-mono text-xs text-faint">up to {maxPoints} points · capped at 100</p>
      </div>

      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Label</th>
              <th>Field</th>
              <th>Op</th>
              <th>Value</th>
              <th>Points</th>
              <th title="If this rule misses, the score is 0 and the lead is flagged">Required</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {drafts.map((d, index) => (
              <tr key={d.key}>
                <td className="min-w-44 px-2">
                  <input
                    aria-label={`Rule ${index + 1} label`}
                    className="input"
                    value={d.label}
                    onChange={(e) => change(d.key, { label: e.target.value })}
                    required
                  />
                </td>
                <td className="min-w-44 px-2">
                  <input
                    aria-label={`Rule ${index + 1} field`}
                    className="input font-mono"
                    list="rubric-fields"
                    value={d.field}
                    onChange={(e) => change(d.key, { field: e.target.value })}
                    required
                  />
                </td>
                <td className="px-2">
                  <select
                    aria-label={`Rule ${index + 1} op`}
                    className="input font-mono"
                    value={d.op}
                    onChange={(e) => change(d.key, { op: e.target.value as Op })}
                  >
                    {OPS.map(({ op, help }) => (
                      <option key={op} value={op} title={help}>
                        {op}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="min-w-32 px-2">
                  <input
                    aria-label={`Rule ${index + 1} value`}
                    className="input font-mono"
                    value={NO_VALUE.includes(d.op) ? "" : d.value}
                    disabled={NO_VALUE.includes(d.op)}
                    placeholder={OPS.find((o) => o.op === d.op)?.help}
                    onChange={(e) => change(d.key, { value: e.target.value })}
                  />
                </td>
                <td className="w-24 px-2">
                  <input
                    aria-label={`Rule ${index + 1} points`}
                    type="number"
                    min={-100}
                    max={100}
                    className="input tabular-nums"
                    value={d.points}
                    onChange={(e) => change(d.key, { points: e.target.value })}
                    required
                  />
                </td>
                <td className="px-2 text-center">
                  <input
                    aria-label={`Rule ${index + 1} required`}
                    type="checkbox"
                    className="size-4 accent-(--accent)"
                    checked={d.required}
                    onChange={(e) => change(d.key, { required: e.target.checked })}
                  />
                </td>
                <td className="px-2">
                  <button
                    type="button"
                    aria-label={`Remove rule ${index + 1}`}
                    className="btn px-2.5 py-1 hover:text-(--red)"
                    disabled={drafts.length === 1}
                    onClick={() => setDrafts((current) => current.filter((x) => x.key !== d.key))}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <datalist id="rubric-fields">
          {FIELDS.map((field) => (
            <option key={field} value={field} />
          ))}
        </datalist>
      </div>

      {save.error && (
        <p role="alert" className="text-sm text-(--red)">
          {describeError(save.error)}
        </p>
      )}

      <div className="flex flex-wrap justify-between gap-2">
        <button
          type="button"
          className="btn"
          disabled={drafts.length >= 50}
          onClick={() => setDrafts((current) => [...current, toDraft({})])}
        >
          <PlusIcon /> Add rule
        </button>
        <div className="flex gap-2">
          {rubric && (
            <button type="button" className="btn" onClick={() => setDrafts(start())}>
              Undo changes
            </button>
          )}
          <button type="submit" className="btn-primary" disabled={save.isPending}>
            {rubric ? `Save as v${latest + 1}` : "Create rubric"}
          </button>
        </div>
      </div>
    </form>
  );
}
