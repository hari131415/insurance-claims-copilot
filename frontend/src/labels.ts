import type { TriageRoute } from "./api";
import { FIELD_DEFS } from "./fields";

// Human-readable labels for API values. The API keeps its snake_case
// values; only the display layer maps them, so nothing here changes
// what the backend stores or returns.

export const ROUTE_LABELS: Record<TriageRoute, string> = {
  ready_for_adjuster: "Ready for adjuster",
  needs_more_info: "Needs more info",
  likely_not_covered: "Likely not covered",
};

const COVERAGE_LABELS: Record<string, string> = {
  policy_found: "Policy found",
  policy_active: "Policy active",
  excluded_loss_type: "Excluded loss type",
  deductible: "Deductible",
  within_limit: "Within limit",
};

function sentenceCase(text: string): string {
  const spaced = text.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export function formatCheckName(name: string): string {
  const sep = name.indexOf(":");
  if (sep === -1) return sentenceCase(name);
  const group = name.slice(0, sep);
  const rest = name.slice(sep + 1);
  if (group === "extraction") {
    return FIELD_DEFS.find((def) => def.key === rest)?.label ?? sentenceCase(rest);
  }
  if (group === "coverage") {
    return COVERAGE_LABELS[rest] ?? sentenceCase(rest);
  }
  if (group === "documents") {
    return sentenceCase(rest);
  }
  return sentenceCase(name);
}
