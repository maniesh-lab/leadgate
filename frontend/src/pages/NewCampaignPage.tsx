/**
 * Create a campaign (admins): what to look for (niche), where (location), which sources
 * to collect from, which rubric scores the leads, and the source options. The API checks
 * the rest (e.g. that OSM knows the niche) and its message is shown as is.
 *
 * With the CSV source, the file is uploaded as soon as it's picked (`POST /api/uploads/csv`)
 * and the campaign points to the stored copy by its id; no file path is ever sent.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type ChangeEvent, type FormEvent, type ReactNode, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { api, describeError } from "../api/client";
import { rubricLabel, useRubrics } from "../api/hooks";
import type { Campaign, CampaignCreate, CsvUpload, SourceName } from "../api/types";
import { PageHeader } from "../components/Page";

const SOURCES: { name: SourceName; title: string; hint: string }[] = [
  { name: "osm", title: "OpenStreetMap", hint: "Free, no key. The default." },
  { name: "google_places", title: "Google Places", hint: "Needs GOOGLE_PLACES_API_KEY." },
  { name: "csv", title: "CSV file", hint: "Your own list, uploaded here." },
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
  csvUploadId: number | null;
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
  csvUploadId: null,
  csvMapping: "",
};

/** The form's text fields -> the API's body. Empty options are left out. */
export function toCampaignBody(form: Form, defaultRubricId: number | undefined): CampaignCreate {
  const options: CampaignCreate["source_options"] = {};
  if (form.maxResults) options.max_results = Number(form.maxResults);
  if (form.radiusKm) options.radius_km = Number(form.radiusKm);
  if (form.country) options.country = form.country.trim();
  if (form.sources.includes("csv")) {
    if (form.csvUploadId) options.csv_upload_id = form.csvUploadId;
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

  const [csvFile, setCsvFile] = useState<CsvUpload | null>(null);
  // The file picked last: an upload of an earlier pick that finishes later is ignored.
  const pickedFile = useRef<File | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) =>
      api.postCsv<CsvUpload>("/api/uploads/csv", file, { filename: file.name }),
    onSuccess: (stored, file) => {
      if (file !== pickedFile.current) return;
      setCsvFile(stored);
      setForm((current) => ({ ...current, csvUploadId: stored.id }));
    },
  });

  function pickFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    pickedFile.current = file;
    setCsvFile(null);
    setForm((current) => ({ ...current, csvUploadId: null }));
    if (file) upload.mutate(file);
  }

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
    if (form.sources.includes("csv") && !form.csvUploadId) {
      setFormError(upload.isPending ? "Wait for the CSV to finish uploading." : "Pick a CSV file.");
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
    <section className="max-w-2xl">
      <PageHeader
        tone="blue"
        eyebrow="Pipeline · new"
        title={
          <>
            New <span className="text-gradient">campaign</span>
          </>
        }
        subtitle="Tell the agents what to look for and where. Nothing runs until you press Start run."
      />
      <form onSubmit={submit} className="card space-y-6">
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
          <legend className="eyebrow mb-2 text-[10px]">Sources</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {SOURCES.map((source) => (
              <label
                key={source.name}
                className="flex cursor-pointer gap-3 rounded-xl border border-line p-3 transition-colors hover:border-line-strong has-checked:border-accent/60 has-checked:bg-(--cyan-bg)"
              >
                <input
                  type="checkbox"
                  aria-label={source.title}
                  checked={form.sources.includes(source.name)}
                  onChange={() => toggleSource(source.name)}
                  className="mt-0.5 accent-accent"
                />
                <span>
                  <span className="block text-sm font-semibold">{source.title}</span>
                  <span className="block text-xs text-muted">{source.hint}</span>
                </span>
              </label>
            ))}
          </div>
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
            <Field
              id="csv-file"
              label="CSV upload"
              hint="UTF-8, a header row, up to 1 MB by default"
            >
              <input
                id="csv-file"
                type="file"
                accept=".csv,text/csv"
                onChange={pickFile}
                className="input file:mr-3 file:cursor-pointer file:rounded-full file:border file:border-line-strong file:bg-transparent file:px-3 file:py-1 file:text-xs file:font-medium file:text-fg"
              />
              <CsvStatus
                pending={upload.isPending}
                stored={csvFile}
                error={upload.error ? describeError(upload.error) : null}
              />
            </Field>
            <Field
              id="csv-mapping"
              label="CSV column mapping"
              hint='JSON, e.g. {"name": "Company", "phone": "Tel"}; empty = columns named like the fields'
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
          <p role="alert" className="text-sm text-(--red)">
            {error}
          </p>
        )}
        <div className="flex gap-2 border-t border-line pt-5">
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

type CsvStatusProps = { pending: boolean; stored: CsvUpload | null; error: string | null };

/** What happened to the picked file: uploading, stored (name, rows, columns) or refused. */
function CsvStatus({ pending, stored, error }: CsvStatusProps) {
  if (pending)
    return (
      <p role="status" className="mt-2 text-xs text-muted">
        Uploading…
      </p>
    );
  if (error)
    return (
      <p role="alert" className="mt-2 text-xs text-(--red)">
        {error}
      </p>
    );
  if (!stored) return null;
  return (
    <p role="status" className="mt-2 text-xs text-muted">
      <span className="font-semibold text-(--green)">Uploaded</span> {stored.filename} ·{" "}
      {stored.row_count} {stored.row_count === 1 ? "row" : "rows"} · columns:{" "}
      <span className="font-mono">{stored.columns.join(", ")}</span>
    </p>
  );
}

type FieldProps = { id: string; label: string; hint?: string; children: ReactNode };

/** A labelled form control; `id` must match the control's id. */
function Field({ id, label, hint, children }: FieldProps) {
  return (
    <div>
      <label htmlFor={id} className="eyebrow text-[10px]">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {hint && <span className="mt-1 block text-xs text-faint">{hint}</span>}
    </div>
  );
}
