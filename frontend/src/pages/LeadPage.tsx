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
import { Fact } from "../components/Fact";
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
  ["mobile_viewport", "Mobile friendly (viewport)"],
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
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{data.name}</h1>
        <StatusBadge status={data.status} />
        {data.opt_out && <Badge tone="red">opted out</Badge>}
        {data.flags.map((flag) => (
          <FlagBadge key={flag} flag={flag} />
        ))}
        <OptOutButton lead={data} />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="card grid grid-cols-2 gap-3 text-sm">
          <Fact label="Phone" value={data.phone} />
          <Fact label="Email" value={data.email} />
          <Fact
            label="Website"
            value={
              data.website && (
                <a href={data.website} target="_blank" rel="noreferrer" className="link">
                  {data.website_domain ?? data.website}
                </a>
              )
            }
          />
          <Fact label="Category" value={data.category} />
          <Fact label="Address" value={data.address} />
          <Fact
            label="Area / city / country"
            value={[data.area, data.city, data.country].filter(Boolean).join(", ")}
          />
          <Fact
            label="Rating"
            value={
              data.rating === null ? null : `${data.rating} (${data.review_count ?? 0} reviews)`
            }
          />
          <Fact label="Business status" value={data.business_status} />
          <Fact label="Established" value={data.established_year} />
          <Fact label="Services" value={data.services.join(", ")} />
          <Fact label="Source" value={`${data.source}: ${data.source_ref}`} />
          <Fact label="CRM record" value={data.crm_record_id} />
          <Fact label="First seen" value={formatDateTime(data.first_seen_at)} />
          <Fact label="Enriched" value={formatDateTime(data.enriched_at)} />
        </div>
        <ScoreReview review={data.review} score={latest?.score ?? data.score} />
      </div>

      <EnrichmentCard enrichment={enrichment} />

      <div>
        <h2 className="mb-2 font-medium">History</h2>
        {data.history.length === 0 ? (
          <p className="text-sm text-slate-500">Not in any run yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Run</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Score</th>
                  <th className="px-3 py-2 font-medium">Decision</th>
                  <th className="px-3 py-2 font-medium">When</th>
                </tr>
              </thead>
              <tbody>
                {data.history.map((item) => (
                  <tr key={item.run_item_id} className="border-b border-slate-100 align-top">
                    <td className="px-3 py-2">
                      <Link to={`/run-items/${item.run_item_id}`} className="link">
                        Run #{item.run_id}
                      </Link>
                      <div className="text-xs text-slate-500">{item.campaign_name}</div>
                    </td>
                    <td className="space-x-1 px-3 py-2">
                      <StatusBadge status={item.status} />
                      <StatusBadge status={item.route} />
                    </td>
                    <td className="px-3 py-2">{item.score ?? "—"}</td>
                    <td className="px-3 py-2">
                      {item.decision ? (
                        <>
                          <StatusBadge status={item.decision} />{" "}
                          <span className="text-slate-500">{item.reviewer_email}</span>
                          {item.reason && (
                            <div className="text-xs text-slate-600">“{item.reason}”</div>
                          )}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
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
    return <p className="card text-sm text-slate-500">The website hasn't been visited yet.</p>;
  }
  const signals = enrichment.signals ?? {};
  const socials = Object.entries(enrichment.socials ?? {}).filter(([, url]) => url);

  return (
    <div className="card grid gap-4 text-sm md:grid-cols-3">
      <div>
        <h2 className="mb-1 font-medium">Website signals</h2>
        <dl className="grid grid-cols-[1fr_auto] gap-x-3">
          {SIGNALS.map(([key, label]) => (
            <div key={key} className="contents">
              <dt className="text-slate-600">{label}</dt>
              <dd>{signalText(signals[key])}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div>
        <h2 className="mb-1 font-medium">Contacts found</h2>
        <p className="text-slate-600">Emails: {enrichment.emails?.join(", ") || "none"}</p>
        <p className="text-slate-600">Phones: {enrichment.phones?.join(", ") || "none"}</p>
        <p className="text-slate-600">
          Social:{" "}
          {socials.length === 0
            ? "none"
            : socials.map(([name, url]) => (
                <a
                  key={name}
                  href={url ?? undefined}
                  target="_blank"
                  rel="noreferrer"
                  className="link mr-2"
                >
                  {name}
                </a>
              ))}
        </p>
      </div>
      <div>
        <h2 className="mb-1 font-medium">Pages fetched</h2>
        {(enrichment.pages ?? []).length === 0 ? (
          <p className="text-slate-500">none (no website)</p>
        ) : (
          <ul className="space-y-0.5">
            {enrichment.pages?.map((page) => (
              <li key={page.url} className="truncate" title={page.url}>
                {page.status ?? "—"} · {page.error ?? page.url}
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
    <span className="ml-auto text-sm">
      <button
        type="button"
        className="btn"
        disabled={optOut.isPending}
        onClick={() => {
          if (window.confirm(`Wipe ${lead.name}'s contact data and never collect it again?`)) {
            optOut.mutate();
          }
        }}
      >
        Opt out
      </button>
      {optOut.error && <span className="ml-2 text-red-700">{describeError(optOut.error)}</span>}
    </span>
  );
}
