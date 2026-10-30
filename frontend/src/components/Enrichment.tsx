/**
 * What the website visit found for a lead: site signals, contacts, pages fetched.
 * `EnrichmentCard` (all of it) is on the lead page; `SignalList` (the signals only, so
 * the dialog stays short) is in the approval queue's decision dialog.
 */
import { EmptyState } from "./Page";

// The shape the enrichment service stores (PROGRESS Decision #63), minus the page text.
export type Enrichment = {
  final_url?: string | null;
  emails?: string[];
  phones?: string[];
  socials?: Record<string, string | null>;
  signals?: Record<string, boolean | number | null>;
  pages?: { url: string; status?: number | null; error?: string | null }[];
};

const SIGNALS: [key: string, label: string][] = [
  ["page_load_ok", "Home page loads"],
  ["https", "Uses https"],
  ["mobile_viewport", "Mobile friendly"],
  ["has_form", "Contact or booking form"],
  ["copyright_year", "Latest copyright year"],
];

/** The site signals as a list. Unknown signals show "—", never "no". */
export function SignalList({ enrichment }: { enrichment: Enrichment }) {
  const signals = enrichment.signals ?? {};
  return (
    <dl className="space-y-1.5">
      {SIGNALS.map(([key, label]) => (
        <div key={key} className="flex justify-between gap-3">
          <dt className="text-muted">{label}</dt>
          <dd className={`font-medium ${signalTone(signals[key])}`}>{signalText(signals[key])}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EnrichmentCard({ enrichment }: { enrichment: Enrichment | null }) {
  if (!enrichment) {
    return <EmptyState title="The website hasn't been visited yet." />;
  }
  const socials = Object.entries(enrichment.socials ?? {}).filter(([, url]) => url);
  const pages = enrichment.pages ?? [];

  return (
    <div className="card grid gap-6 text-sm md:grid-cols-3">
      <div>
        <h2 className="eyebrow mb-3">Website signals</h2>
        <SignalList enrichment={enrichment} />
      </div>
      <div>
        <h2 className="eyebrow mb-3">Contacts found</h2>
        <dl className="space-y-2">
          <div>
            <dt className="text-xs text-faint">Emails</dt>
            <dd className="break-words">{enrichment.emails?.join(", ") || "none"}</dd>
          </div>
          <div>
            <dt className="text-xs text-faint">Phones</dt>
            <dd>{enrichment.phones?.join(", ") || "none"}</dd>
          </div>
          <div>
            <dt className="text-xs text-faint">Social</dt>
            <dd className="flex flex-wrap gap-x-3">
              {socials.length === 0
                ? "none"
                : socials.map(([name, url]) => (
                    <a
                      key={name}
                      href={url ?? undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="link"
                    >
                      {name}
                    </a>
                  ))}
            </dd>
          </div>
        </dl>
      </div>
      <div className="min-w-0">
        <h2 className="eyebrow mb-3">Pages fetched</h2>
        {pages.length === 0 ? (
          <p className="text-muted">none (no website)</p>
        ) : (
          <ul className="space-y-1.5">
            {pages.map((page) => (
              <li key={page.url} className="flex gap-2" title={page.url}>
                <span
                  className={`font-mono text-xs font-semibold ${page.status === 200 ? "text-(--green)" : "text-(--amber)"}`}
                >
                  {page.status ?? "—"}
                </span>
                <span className="truncate text-muted">{page.error ?? page.url}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** true -> "yes", false -> "no", a year as is; null = the crawler couldn't tell. */
function signalText(value: boolean | number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

function signalTone(value: boolean | number | null | undefined): string {
  if (value === true) return "text-(--green)";
  if (value === false) return "text-(--red)";
  return "text-fg";
}
