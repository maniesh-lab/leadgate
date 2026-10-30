/**
 * Short names for the API's shapes. They all come from `schema.d.ts`, which
 * `npm run gen:api` generates from the API's OpenAPI document, so a change on the backend
 * shows up here as a type error instead of a bug in the browser.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type User = Schemas["UserOut"];
export type UserRole = Schemas["UserRole"];
export type Token = Schemas["Token"];

export type Rubric = Schemas["RubricOut"];
export type Campaign = Schemas["CampaignOut"];
export type CampaignCreate = Schemas["CampaignCreate"];
export type SourceName = CampaignCreate["sources"][number];

export type Run = Schemas["RunOut"];
export type RunDetail = Schemas["RunDetail"];
export type RunStatus = Schemas["RunStatus"];
export type RunItemRow = Schemas["RunItemRow"];
export type RunItemDetail = Schemas["RunItemDetail"];
export type RunItemStatus = Schemas["RunItemStatus"];
export type Step = Schemas["StepOut"];

export type LeadRow = Schemas["LeadRow"];
export type LeadDetail = Schemas["LeadDetail"];
export type LeadStatus = Schemas["LeadStatus"];
export type Review = Schemas["Review"];

export type QueueItem = Schemas["QueueItem"];
export type Decision = Schemas["Decision"];
export type Approval = Schemas["ApprovalOut"];

export type AuditRow = Schemas["AuditOut"];
export type ActorType = Schemas["ActorType"];

export type Rule = Schemas["Rule"];

export type ReportName = Schemas["ReportName"];
export type Report = Schemas["ReportOut"];

/** Every list endpoint answers with one page: `?page=2&page_size=50` -> items 51-100. */
export type Page<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};
