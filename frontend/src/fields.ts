import type { ExtractedClaim } from "./api";

// Same threshold the backend triage uses to flag low-confidence fields.
export const LOW_CONFIDENCE = 0.6;

export type FieldKey = keyof ExtractedClaim;

export interface FieldDef {
  key: FieldKey;
  label: string;
  kind: "text" | "textarea" | "date" | "number" | "select";
  options?: string[];
}

export const FIELD_DEFS: FieldDef[] = [
  { key: "policy_number", label: "Policy number", kind: "text" },
  { key: "claimant_name", label: "Claimant name", kind: "text" },
  { key: "date_of_loss", label: "Date of loss", kind: "date" },
  {
    key: "loss_type",
    label: "Loss type",
    kind: "select",
    options: ["auto", "property", "theft", "water damage"],
  },
  { key: "incident_location", label: "Incident location", kind: "text" },
  { key: "incident_description", label: "Incident description", kind: "textarea" },
  { key: "estimated_damage_amount", label: "Estimated damage (USD)", kind: "number" },
  { key: "vehicle_or_property_details", label: "Vehicle / property details", kind: "textarea" },
];

export function formatValue(value: string | number | null): string {
  if (value === null) return "—";
  if (typeof value === "number") return `$${value.toFixed(2)}`;
  return value;
}
