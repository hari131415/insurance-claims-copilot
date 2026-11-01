import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  fileContentUrl,
  getClaim,
  getHealth,
  runTriage,
  triggerExtraction,
  updateExtraction,
  uploadFiles,
  type ClaimFile,
  type ExtractedClaim,
} from "../api";
import FieldRow, { withValue } from "../components/FieldRow";
import { FIELD_DEFS, formatValue } from "../fields";
import { ROUTE_LABELS, formatCheckName } from "../labels";

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-gray-200 text-gray-700",
  processing: "bg-blue-100 text-blue-800",
  done: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
};

const ROUTE_STYLES: Record<string, string> = {
  ready_for_adjuster: "bg-green-100 text-green-800",
  needs_more_info: "bg-amber-100 text-amber-800",
  likely_not_covered: "bg-red-100 text-red-800",
};

// The consistency reason embeds the judge's full notes, which are
// also shown under Consistency below; the list keeps one line.
function reasonSummary(reason: string): string {
  return reason.startsWith("Consistency check flagged a problem:")
    ? "Consistency check flagged a problem (see Consistency below)."
    : reason;
}

export default function ReviewPage() {
  const { id = "" } = useParams();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<ExtractedClaim | null>(null);
  const [extraFiles, setExtraFiles] = useState<File[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [previewFile, setPreviewFile] = useState<ClaimFile | null>(null);

  const claimQuery = useQuery({
    queryKey: ["claim", id],
    queryFn: () => getClaim(id),
    refetchInterval: (query) =>
      query.state.data?.extraction_status === "processing" ? 1500 : false,
  });

  // Same query key as the header, so this shares App's cached health
  // and adds no request; it only decides the usage line's wording.
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["claim", id] });

  const extract = useMutation({
    mutationFn: () => triggerExtraction(id),
    onSuccess: () => {
      setDraft(null);
      setFormError(null);
      void invalidate();
    },
    onError: (err) => setFormError(err instanceof Error ? err.message : "Extraction failed"),
  });

  const triage = useMutation({
    mutationFn: () => runTriage(id),
    onSuccess: () => {
      setFormError(null);
      void invalidate();
    },
    onError: (err) => setFormError(err instanceof Error ? err.message : "Triage failed"),
  });

  const save = useMutation({
    mutationFn: (claim: ExtractedClaim) => updateExtraction(id, claim),
    onSuccess: () => {
      setDraft(null);
      setFormError(null);
      void invalidate();
    },
    onError: (err) => setFormError(err instanceof Error ? err.message : "Save failed"),
  });

  const upload = useMutation({
    mutationFn: () => uploadFiles(id, extraFiles),
    onSuccess: () => {
      setExtraFiles([]);
      setFormError(null);
      void invalidate();
    },
    onError: (err) => setFormError(err instanceof Error ? err.message : "Upload failed"),
  });

  if (claimQuery.isPending) return <p className="text-sm text-gray-500">Loading claim…</p>;
  if (claimQuery.isError || !claimQuery.data) {
    return (
      <p role="alert" className="text-sm text-red-700">
        Could not load this claim. <Link to="/" className="underline">Back home</Link>
      </p>
    );
  }

  const claim = claimQuery.data;
  const shown = draft ?? claim.extraction;
  const classificationByFile = new Map(
    claim.classifications.map((item) => [item.filename, item]),
  );

  const openPreview = (filename: string) => {
    const file = claim.files.find((item) => item.filename === filename);
    if (file) setPreviewFile(file);
  };

  const triageStale = (() => {
    if (!claim.triage?.triaged_at) return false;
    const triagedAt = new Date(claim.triage.triaged_at).getTime();
    if (claim.overrides.some((o) => new Date(o.edited_at).getTime() > triagedAt)) return true;
    return (
      claim.extraction_completed_at !== null &&
      new Date(claim.extraction_completed_at).getTime() > triagedAt
    );
  })();

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Claim {claim.id.slice(0, 8)}…</h2>
        <span
          className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLES[claim.extraction_status] ?? ""}`}
        >
          {claim.extraction_status}
        </span>
      </div>
      <p className="break-all text-xs text-gray-500">{claim.id}</p>

      {formError && (
        <p role="alert" className="text-sm text-red-700">
          {formError}
        </p>
      )}

      <section className="rounded border border-gray-200 bg-white p-6">
        <h3 className="mb-3 font-medium">Files</h3>
        {claim.files.length === 0 && (
          <p className="mb-3 text-sm text-gray-500">No files uploaded yet.</p>
        )}
        <ul className="mb-4 space-y-1 text-sm">
          {claim.files.map((file) => {
            const classification = classificationByFile.get(file.filename);
            return (
              <li key={file.id}>
                <button
                  type="button"
                  onClick={() => setPreviewFile(file)}
                  className="text-blue-600 underline"
                >
                  {file.filename}
                </button>{" "}
                · {file.detected_content_type ?? file.content_type} ·{" "}
                {(file.size_bytes / 1024).toFixed(1)} KB
                {classification && (
                  <span className="text-gray-500">
                    {" "}
                    · {classification.document_type} ({classification.confidence.toFixed(2)})
                  </span>
                )}
              </li>
            );
          })}
        </ul>
        <div className="flex items-center gap-2">
          <label htmlFor="extra-files" className="text-sm font-medium">
            Add files
          </label>
          <input
            id="extra-files"
            type="file"
            multiple
            accept=".jpg,.jpeg,.png,.pdf"
            onChange={(e) => setExtraFiles(Array.from(e.target.files ?? []))}
            className="text-sm"
          />
          <button
            type="button"
            disabled={extraFiles.length === 0 || upload.isPending}
            onClick={() => upload.mutate()}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Upload
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-500">
          Files added after extraction are classified on the next extraction.
        </p>
      </section>

      <section className="rounded border border-gray-200 bg-white p-6">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-medium">Extraction</h3>
          <button
            type="button"
            disabled={claim.files.length === 0 || extract.isPending}
            onClick={() => extract.mutate()}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {claim.extraction_status === "processing" ? "Extracting…" : "Run extraction"}
          </button>
        </div>
        {claim.extraction_status === "processing" && (
          <p className="text-sm text-gray-500">
            Extraction is running; this page refreshes automatically.
          </p>
        )}
        {claim.extraction_status === "failed" && claim.extraction_error && (
          <p role="alert" className="text-sm text-red-700">
            Extraction failed: {claim.extraction_error}
          </p>
        )}
        {claim.extraction_status === "pending" && (
          <p className="text-sm text-gray-500">Upload files, then run extraction.</p>
        )}
        {shown && (
          <div className={previewFile ? "grid gap-6 lg:grid-cols-2" : ""}>
            <div>
              <div className={previewFile ? "grid gap-3" : "grid gap-3 md:grid-cols-2"}>
                {FIELD_DEFS.map((def) => {
                  const source = shown[def.key].source_file;
                  return (
                    <FieldRow
                      key={def.key}
                      def={def}
                      field={shown[def.key]}
                      onChange={(value) => setDraft(withValue(shown, def.key, value))}
                      onSourceClick={source ? () => openPreview(source) : undefined}
                    />
                  );
                })}
              </div>
              {draft && (
                <div className="mt-4 flex gap-2">
                  <button
                    type="button"
                    disabled={save.isPending}
                    onClick={() => save.mutate(draft)}
                    className="rounded bg-green-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                  >
                    Save edits
                  </button>
                  <button
                    type="button"
                    onClick={() => setDraft(null)}
                    className="rounded border border-gray-300 px-4 py-2 text-sm"
                  >
                    Discard
                  </button>
                </div>
              )}
              {claim.usage && (
                <p className="mt-4 text-xs text-gray-500">
                  {healthQuery.data?.llm_provider === "recorded"
                    ? `Recorded result · original cost $${claim.usage.cost_usd.toFixed(4)}`
                    : `${claim.usage.input_tokens} input / ${claim.usage.output_tokens} output tokens · $${claim.usage.cost_usd.toFixed(4)}`}
                </p>
              )}
            </div>
            {previewFile && (
              <aside className="self-start rounded border border-gray-200 bg-gray-50 p-4 lg:sticky lg:top-4">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <h4 className="break-all font-medium">{previewFile.filename}</h4>
                  <button
                    type="button"
                    onClick={() => setPreviewFile(null)}
                    className="shrink-0 rounded border border-gray-300 px-2 py-1 text-sm"
                  >
                    Close preview
                  </button>
                </div>
                {(previewFile.detected_content_type ?? previewFile.content_type).startsWith(
                  "image/",
                ) ? (
                  <img
                    src={fileContentUrl(claim.id, previewFile.id)}
                    alt={previewFile.filename}
                    className="max-w-full"
                  />
                ) : (
                  (previewFile.detected_content_type ?? previewFile.content_type) ===
                  "application/pdf"
                ) ? (
                  <iframe
                    title={`Preview of ${previewFile.filename}`}
                    // Chrome's PDF viewer parameter: fit the page to
                    // the pane width instead of shrinking the whole
                    // page into view, which rendered scanned pages tiny.
                    src={`${fileContentUrl(claim.id, previewFile.id)}#view=FitH`}
                    className="h-[600px] w-full"
                  />
                ) : (
                  <p className="text-sm text-gray-500">
                    Preview is not available for this file type.
                  </p>
                )}
              </aside>
            )}
          </div>
        )}
      </section>

      {claim.overrides.length > 0 && (
        <section className="rounded border border-gray-200 bg-white p-6">
          <h3 className="mb-3 font-medium">Adjuster edits</h3>
          <ul className="space-y-1 text-sm">
            {claim.overrides.map((override) => (
              <li key={override.field}>
                <span className="font-medium">
                  {FIELD_DEFS.find((def) => def.key === override.field)?.label ??
                    override.field}
                </span>
                :{" "}
                {formatValue(
                  typeof override.value === "string" || typeof override.value === "number"
                    ? override.value
                    : null,
                )}{" "}
                (model said{" "}
                {formatValue(
                  typeof override.model_value === "string" ||
                    typeof override.model_value === "number"
                    ? override.model_value
                    : null,
                )}
                )
                {override.conflicts_with_model && (
                  <span className="ml-2 font-semibold text-amber-700">
                    · re-extraction disagreed
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded border border-gray-200 bg-white p-6">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="font-medium">Triage</h3>
          <button
            type="button"
            disabled={!claim.extraction || triage.isPending}
            onClick={() => triage.mutate()}
            className="rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            Run triage
          </button>
        </div>
        {claim.triage && triageStale && (
          <div
            role="status"
            aria-label="Triage is out of date: fields changed since it ran."
            className="mb-4 rounded border border-amber-300 bg-amber-50 p-3"
          >
            <p className="text-sm font-medium text-amber-800">
              Triage is out of date: fields changed since it ran.
            </p>
            <button
              type="button"
              disabled={triage.isPending}
              onClick={() => triage.mutate()}
              className="mt-2 rounded bg-blue-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              Re-run triage
            </button>
          </div>
        )}
        {!claim.triage && (
          <p className="text-sm text-gray-500">
            {claim.extraction ? "Not triaged yet." : "Run extraction first, then triage."}
          </p>
        )}
        {claim.triage && (
          <>
            <p className="mb-3">
              {/* A status region's accessible name does not come from its
              content, so the label mirrors the visible route for role queries. */}
              <span
                role="status"
                aria-label={ROUTE_LABELS[claim.triage.route]}
                className={`rounded px-2 py-0.5 text-xs ${ROUTE_STYLES[claim.triage.route] ?? ""}`}
              >
                {ROUTE_LABELS[claim.triage.route]}
              </span>
            </p>
            <ul className="mb-4 list-inside list-disc space-y-1 text-sm">
              {claim.triage.reasons.map((reason) => (
                <li key={reason}>{reasonSummary(reason)}</li>
              ))}
            </ul>
            <table className="w-full text-sm">
              <tbody>
                {claim.triage.checks.map((check) => {
                  const overridden =
                    check.name.startsWith("extraction:") &&
                    claim.overrides.some(
                      (o) => o.field === check.name.slice("extraction:".length),
                    );
                  return (
                    <tr key={check.name} className="border-t border-gray-100">
                      <td className="py-1 pr-2 font-medium">{formatCheckName(check.name)}</td>
                      <td className="py-1 pr-2">{check.passed ? "✓" : "✗"}</td>
                      <td className="py-1 text-gray-600">
                        {overridden ? "Set by adjuster" : check.detail}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {claim.triage.consistency && (
              <p className="mt-3 text-sm">
                Consistency: {claim.triage.consistency.consistent ? "consistent" : "inconsistent"} —{" "}
                {claim.triage.consistency.notes}
              </p>
            )}
            <p className="mt-3 text-xs text-gray-500">
              Triaged{" "}
              {claim.triage.triaged_at ? new Date(claim.triage.triaged_at).toLocaleString() : "—"} ·{" "}
              policy version {claim.triage.policy_version?.slice(0, 12) ?? "—"}
            </p>
          </>
        )}
      </section>
    </div>
  );
}
