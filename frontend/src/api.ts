const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type ExtractionStatus = "pending" | "processing" | "done" | "failed";
export type TriageRoute = "ready_for_adjuster" | "needs_more_info" | "likely_not_covered";

export interface FieldProvenance {
  value: string | number | null;
  confidence: number;
  source_file: string | null;
}

export interface ExtractedClaim {
  policy_number: FieldProvenance;
  claimant_name: FieldProvenance;
  date_of_loss: FieldProvenance;
  loss_type: FieldProvenance;
  incident_location: FieldProvenance;
  incident_description: FieldProvenance;
  estimated_damage_amount: FieldProvenance;
  vehicle_or_property_details: FieldProvenance;
}

export interface FileClassification {
  filename: string;
  document_type: string;
  confidence: number;
}

export interface FieldOverride {
  field: string;
  value: unknown;
  model_value: unknown;
  edited_at: string;
  conflicts_with_model: boolean;
}

export interface TriageCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface ConsistencyResult {
  consistent: boolean;
  notes: string;
}

export interface TriageResult {
  route: TriageRoute;
  reasons: string[];
  checks: TriageCheck[];
  consistency: ConsistencyResult | null;
  triaged_at: string | null;
  policy_version: string | null;
}

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface ClaimFile {
  id: string;
  filename: string;
  content_type: string;
  detected_content_type: string | null;
  size_bytes: number;
  created_at: string;
}

export interface Claim {
  id: string;
  extraction_status: ExtractionStatus;
  extraction_completed_at: string | null;
  extraction_error: string | null;
  extraction: ExtractedClaim | null;
  model_extraction: ExtractedClaim | null;
  overrides: FieldOverride[];
  classifications: FileClassification[];
  usage: TokenUsage | null;
  triage: TriageResult | null;
  files: ClaimFile[];
  created_at: string;
  updated_at: string;
}

export interface ClaimListItem {
  id: string;
  extraction_status: ExtractionStatus;
  file_count: number;
  claimant_name: string | null;
  policy_number: string | null;
  triage_route: TriageRoute | null;
  created_at: string;
}

export interface Health {
  status: string;
  llm_provider: string;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, init);
  if (!res.ok) {
    let detail = `Request failed: ${res.status}`;
    try {
      const body: unknown = await res.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const value = (body as { detail: unknown }).detail;
        if (typeof value === "string") detail = value;
      }
    } catch {
      // Keep the default message when the error body is not JSON.
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export function listClaims(): Promise<ClaimListItem[]> {
  return request<ClaimListItem[]>("/claims");
}

export function getHealth(): Promise<Health> {
  return request<Health>("/health");
}

export function createClaim(): Promise<Claim> {
  return request<Claim>("/claims", { method: "POST" });
}

export function getClaim(id: string): Promise<Claim> {
  return request<Claim>(`/claims/${id}`);
}

export function uploadFiles(id: string, files: File[]): Promise<Claim> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  return request<Claim>(`/claims/${id}/files`, { method: "POST", body: form });
}

export function triggerExtraction(id: string): Promise<Claim> {
  return request<Claim>(`/claims/${id}/extract`, { method: "POST" });
}

export function runTriage(id: string): Promise<Claim> {
  return request<Claim>(`/claims/${id}/triage`, { method: "POST" });
}

export function updateExtraction(id: string, claim: ExtractedClaim): Promise<Claim> {
  return request<Claim>(`/claims/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(claim),
  });
}

export function fileContentUrl(claimId: string, fileId: string): string {
  return `${API_URL}/claims/${claimId}/files/${fileId}/content`;
}
