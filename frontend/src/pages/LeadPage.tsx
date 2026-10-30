/**
 * Everything about one lead: its cleaned fields, what the website visit found (emails,
 * phones, social links, site signals, pages fetched), the latest score breakdown, and
 * every run it was in with the human's decision. Admins can opt the lead out here
 * (wipes its contact data; the API also rejects its waiting items).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api, describeError } from "../api/client";
import type { LeadDetail } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { Badge, FlagBadge, StatusBadge } from "../components/Badge";
import { ExternalIcon } from "../components/icons";
import { EmptyState, Fact, PageHeader } from "../components/Page";
import { ErrorMessage, Loading } from "../components/QueryState";
import { ScoreReview } from "../components/ScoreReview";
import { formatDateTime } from "../lib/format";

// The shape the enrichment service stores (PROGRESS Decision #63), minus the page text.
type Enrichment = {
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

export function LeadPage() {
  const { leadId } = useParams();
  const lead = useQuery({
    queryKey: ["leads", Number(leadId)],
    queryFn: () => api.get<LeadDetail>(`/api/leads/${leadId}`),
  });

  if (lead.isPending) return <Loading />;
  if (lead.error) return <ErrorMessage error={lead.error} />;
  const data = lead.data;
  const enrichment = (data.enrichment ?? null) as Enrichment | null;
  const latest = data.history[0];

  return (
    <section className="space-y-6">
      <PageHeader
        eyebrow={`Lead · ${data.category ?? data.source}`}
        title={data.name}
        subtitle={[data.address, data.city].filter(Boolean).join(" · ") || undefined}
        actions={
          <>
            <StatusBadge status={data.status} />
            {data.opt_out && <Badge tone="red">opted out</Badge>}
            <OptOutButton lead={data} />
          </>
        }
      />

      {data.flags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {data.flags.map((flag) => (
            <FlagBadge key={flag} flag={flag} />
          ))}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[3fr_2fr]">
        <div className="card grid content-start gap-5 sm:grid-cols-2">
          <Fact label="Phone" value={data.phone} />
          <Fact label="Email" value={data.email} />
          <Fact
            label="Website"
            value={
              data.website && (
                <a
                  href={data.website}
                  target="_blank"
                  rel="noreferrer"
                  className="link inline-flex items-center gap-1"
                >
                  {data.website_domain ?? data.website}
                  <ExternalIcon className="size-3" />
                </a>
              )
            }
          />
          <Fact label="Category" value={data.category} />
          <Fact
            label="Area / city / country"
            value={[data.area, data.city, data.country].filter(Boolean).join(", ")}
          />
          <Fact
            label="Rating"
            value={
              data.rating === null ? null : `${data.rating} ★ (${data.review_count ?? 0} reviews)`
            }
          />
          <Fact label="Business status" value={data.business_status} />
          <Fact label="Established" value={data.established_year} />
          <div className="sm:col-span-2">
            <Fact label="Services" value={data.services.join(", ")} />
          </div>
          <Fact
            label="Source"
            value={
              <span className="font-mono text-xs">{`${data.source}: ${data.source_ref}`}</span>
            }
          />
          <Fact label="CRM record" value={data.crm_record_id} />
          <Fact label="First seen" value={formatDateTime(data.first_seen_at)} />
          <Fact label="Enriched" value={data.enriched_at && formatDateTime(data.enriched_at)} />
        </div>
        <ScoreReview review={data.review} score={latest?.score ?? data.score} />
      </div>

      <EnrichmentCard enrichment={enrichment} />

      <div>
        <h2 className="mb-3 text-lg font-semibold">History</h2>
        {data.history.length === 0 ? (
          <EmptyState title="Not in any run yet." />
        ) : (
          <div className="card overflow-x-auto p-0">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Status</th>
                  <th className="text-right">Score</th>
                  <th>Decision</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((item) => (
                  <tr key={item.run_item_id}>
                    <td>
                      <Link
                        to={`/run-items/${item.run_item_id}`}
                        className="link font-mono font-semibold"
                      >
                        Run #{item.run_id}
                      </Link>
                      <div className="mt-0.5 text-xs text-muted">{item.campaign_name}</div>
                    </td>
                    <td>
                      <div className="flex flex-wrap gap-1">
                        <StatusBadge status={item.status} />
                        <StatusBadge status={item.route} />
                      </div>
                    </td>
                    <td className="text-right font-semibold tabular-nums">{item.score ?? "—"}</td>
                    <td>
                      {item.decision ? (
                        <>
                          <StatusBadge status={item.decision} />
                          <div className="mt-1 text-xs text-muted">{item.reviewer_email}</div>
                          {item.reason && (
                            <div className="mt-0.5 text-xs text-muted">“{item.reason}”</div>
                          )}
                        </>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap text-muted">
                      {formatDateTime(item.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

/** What the website visit found. Unknown signals show "—", never "no". */
function EnrichmentCard({ enrichment }: { enrichment: Enrichment | null }) {
  if (!enrichment) {
    return <EmptyState title="The website hasn't been visited yet." />;
  }
  const signals = enrichment.signals ?? {};
  const socials = Object.entries(enrichment.socials ?? {}).filter(([, url]) => url);
  const pages = enrichment.pages ?? [];

  return (
    <div className="card grid gap-6 text-sm md:grid-cols-3">
      <div>
        <h2 className="eyebrow mb-3">Website signals</h2>
        <dl className="space-y-1.5">
          {SIGNALS.map(([key, label]) => (
            <div key={key} className="flex justify-between gap-3">
              <dt className="text-muted">{label}</dt>
              <dd className={`font-medium ${signalTone(signals[key])}`}>
                {signalText(signals[key])}
              </dd>
            </div>
          ))}
        </dl>
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

/** Admins only: wipe the lead's contact data for good (asks first). */
function OptOutButton({ lead }: { lead: LeadDetail }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const optOut = useMutation({
    mutationFn: () => api.delete(`/api/leads/${lead.id}/data`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["leads"] }),
  });

  if (user?.role !== "admin" || lead.opt_out) return null;
  return (
    <span className="flex items-center gap-2 text-sm">
      <button
        type="button"
        className="btn px-3 py-1.5 hover:border-(--red)/50 hover:text-(--red)"
        disabled={optOut.isPending}
        onClick={() => {
          if (window.confirm(`Wipe ${lead.name}'s contact data and never collect it again?`)) {
            optOut.mutate();
          }
        }}
      >
        Opt out
      </button>
      {optOut.error && <span className="text-(--red)">{describeError(optOut.error)}</span>}
    </span>
  );
}
