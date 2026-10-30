/**
 * Queries several pages share: the rubrics (to show "web-agency v1" instead of an id) and
 * every campaign (for the campaign filters). Both lists are small, so one page of 200
 * holds them all.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { Campaign, Page, Rubric } from "./types";

export function useRubrics() {
  return useQuery({
    queryKey: ["rubrics", "all"],
    queryFn: () => api.get<Page<Rubric>>("/api/rubrics", { page_size: 200 }),
    select: (page) => page.items,
  });
}

export function useAllCampaigns() {
  return useQuery({
    queryKey: ["campaigns", "all"],
    queryFn: () => api.get<Page<Campaign>>("/api/campaigns", { page_size: 200 }),
    select: (page) => page.items,
  });
}

export function rubricLabel(rubric: Pick<Rubric, "name" | "version">): string {
  return `${rubric.name} v${rubric.version}`;
}
