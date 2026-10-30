/**
 * Create a campaign (admins): what to look for (niche), where (location), which sources
 * to collect from, which rubric scores the leads, and the source options. The API checks
 * the rest (e.g. that OSM knows the niche) and its message is shown as is.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, type ReactNode, useState } from "react";
import { useNavigate } from "react-router";
import { api, describeError } from "../api/client";
import { rubricLabel, useRubrics } from "../api/hooks";
import type { Campaign, CampaignCreate, SourceName } from "../api/types";

const SOURCES: { name: SourceName; label: string }[] = [
  { name: "osm", label: "OpenStreetMap (free, no key)" },
  { name: "google_places", label: "Google Places (needs GOOGLE_PLACES_API_KEY)" },
  { name: "csv", label: "CSV file on the worker's disk" },
];

type Form = {
  name: string;
  niche: string;
  location: string;
  sources: SourceName[];
  rubricId: string;
  maxResults: string;
  radiusKm: string;
  country: string;
  csvPath: string;
  csvMapping: string;
};

const EMPTY: Form = {
  name: "",
  niche: "",
  location: "",
  sources: ["osm"],
  rubricId: "",
  maxResults: "",
  radiusKm: "",
  country: "",
  csvPath: "",
  csvMapping: "",
};

/** The form's text fields -> the API's body. Empty options are left out. */
export function toCampaignBody(form: Form, defaultRubricId: number | undefined): CampaignCreate {
  const options: CampaignCreate["source_options"] = {};
  if (form.maxResults) options.max_results = Number(form.maxResults);
  if (form.radiusKm) options.radius_km = Number(form.radiusKm);
  if (form.country) options.country = form.country.trim();
  if (form.sources.includes("csv")) {
    if (form.csvPath) options.csv_path = form.csvPath.trim();
    // A mapping like {"name": "Company", "phone": "Tel"}; JSON.parse throws on a typo.
    if (form.csvMapping.trim()) options.csv_mapping = JSON.parse(form.csvMapping);
  }
  return {
    name: form.name,
    niche: form.niche,
    location: form.location,
    sources: form.sources,
    source_options: options,
    rubric_id: form.rubricId ? Number(form.rubricId) : (defaultRubricId ?? 0),
  };
}

export function NewCampaignPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const rubrics = useRubrics();
  const [form, setForm] = useState<Form>(EMPTY);
  const [formError, setFormError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (body: CampaignCreate) => api.post<Campaign>("/api/campaigns", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      navigate("/campaigns");
    },
  });

  const set = (field: keyof Form) => (event: { target: { value: string } }) =>
    setForm({ ...form, [field]: event.target.value });

  function toggleSource(source: SourceName) {
    const sources = form.sources.includes(source)
      ? form.sources.filter((s) => s !== source)
      : [...form.sources, source];
    setForm({ ...form, sources });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setFormError(null);
    if (form.sources.length === 0) {
      setFormError("Pick at least one source.");
      return;
    }
    try {
      create.mutate(toCampaignBody(form, rubrics.data?.[0]?.id));
    } catch {
      setFormError("The CSV column mapping isn't valid JSON.");
    }
  }

  const error = formError ?? (create.error ? describeError(create.error) : null);

  return (
    <section className="max-w-xl">
      <h1 className="mb-4 text-lg font-semibold">New campaign</h1>
      <form onSubmit={submit} className="card space-y-4">
        <Field id="name" label="Name">
          <input id="name" required value={form.name} onChange={set("name")} className="input" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id="niche" label="Niche" hint="e.g. dentist, plumber, restaurant">
            <input
              id="niche"
              required
              value={form.niche}
              onChange={set("niche")}
              className="input"
            />
          </Field>
          <Field id="location" label="Location" hint="e.g. Austin, TX">
            <input
              id="location"
              required
              value={form.location}
              onChange={set("location")}
              className="input"
            />
          </Field>
        </div>

        <fieldset>
          <legend className="text-sm text-slate-600">Sources</legend>
          {SOURCES.map((source) => (
            <label key={source.name} className="mt-1 flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={form.sources.includes(source.name)}
                onChange={() => toggleSource(source.name)}
              />
              {source.label}
            </label>
          ))}
        </fieldset>

        <Field id="rubric" label="Scoring rubric">
          <select id="rubric" value={form.rubricId} onChange={set("rubricId")} className="input">
            {rubrics.data?.map((rubric) => (
              <option key={rubric.id} value={rubric.id}>
                {rubricLabel(rubric)}
              </option>
            ))}
          </select>
        </Field>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field id="max-results" label="Max results" hint="per source; empty = 50">
            <input
              id="max-results"
              type="number"
              min={1}
              max={500}
              value={form.maxResults}
              onChange={set("maxResults")}
              className="input"
            />
          </Field>
          <Field id="radius-km" label="Radius (km)" hint="empty = the city's boundary">
            <input
              id="radius-km"
              type="number"
              min={0.1}
              max={50}
              step="any"
              value={form.radiusKm}
              onChange={set("radiusKm")}
              className="input"
            />
          </Field>
          <Field id="country" label="Country" hint="2 letters, for phones">
            <input
              id="country"
              maxLength={2}
              value={form.country}
              onChange={set("country")}
              className="input"
            />
          </Field>
        </div>

        {form.sources.includes("csv") && (
          <>
            <Field id="csv-path" label="CSV path" hint="a file the worker can read">
              <input
                id="csv-path"
                value={form.csvPath}
                onChange={set("csvPath")}
                className="input"
              />
            </Field>
            <Field
              id="csv-mapping"
              label="CSV column mapping"
              hint='JSON, e.g. {"name": "Company", "phone": "Tel"}'
            >
              <textarea
                id="csv-mapping"
                rows={3}
                value={form.csvMapping}
                onChange={set("csvMapping")}
                className="input font-mono"
              />
            </Field>
          </>
        )}

        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          <button type="submit" className="btn-primary" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create campaign"}
          </button>
          <button type="button" className="btn" onClick={() => navigate("/campaigns")}>
            Cancel
          </button>
        </div>
      </form>
    </section>
  );
}

type FieldProps = { id: string; label: string; hint?: string; children: ReactNode };

/** A labelled form control; `id` must match the control's id. */
function Field({ id, label, hint, children }: FieldProps) {
  return (
    <div>
      <label htmlFor={id} className="text-sm text-slate-600">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {hint && <span className="text-xs text-slate-400">{hint}</span>}
    </div>
  );
}
