/**
 * One quiet line under every logged-in page: whether the API is up (a dot from
 * `/api/ready`, checked every minute), the app version, the OpenStreetMap credit
 * (OSM's licence asks for it wherever its data shows), the GitHub link and the author.
 */
import { useQuery } from "@tanstack/react-query";
import { version } from "../../package.json";
import { api } from "../api/client";

const REPO = "https://github.com/maniesh-lab/leadgate";

export function Footer() {
  const ready = useQuery({
    queryKey: ["ready"],
    queryFn: () => api.get<{ status: string }>("/api/ready"),
    refetchInterval: 60_000,
    retry: false,
  });
  const [dot, status] = ready.isPending
    ? ["bg-faint", "checking API"]
    : ready.isSuccess
      ? ["bg-(--green)", "API ok"]
      : ["bg-(--red)", "API down"];

  return (
    <footer className="mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 pb-8 font-mono text-[11px] text-faint">
      <span className="flex items-center gap-1.5" title="GET /api/ready">
        <span className={`size-1.5 rounded-full ${dot}`} />
        {status}
      </span>
      <span aria-hidden="true">·</span>
      <span>v{version}</span>
      <span aria-hidden="true">·</span>
      <a
        href="https://www.openstreetmap.org/copyright"
        target="_blank"
        rel="noreferrer"
        className="hover:text-fg"
      >
        © OpenStreetMap contributors
      </a>
      <span aria-hidden="true">·</span>
      <a href={REPO} target="_blank" rel="noreferrer" className="hover:text-fg">
        GitHub
      </a>
      <span aria-hidden="true">·</span>
      <span>Built by Manish Pandeya</span>
    </footer>
  );
}
