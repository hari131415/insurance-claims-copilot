import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createClaim, listClaims, uploadFiles } from "../api";
import { ROUTE_LABELS } from "../labels";

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

export default function UploadPage() {
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const claimsQuery = useQuery({ queryKey: ["claims"], queryFn: listClaims });

  const submit = useMutation({
    mutationFn: async () => {
      const claim = await createClaim();
      setCreatedId(claim.id);
      if (files.length > 0) await uploadFiles(claim.id, files);
      return claim;
    },
    onSuccess: (claim) => navigate(`/claims/${claim.id}`),
    onError: (err) => setError(err instanceof Error ? err.message : "Upload failed"),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    submit.mutate();
  };

  return (
    <div className="space-y-8">
      <section className="rounded border border-gray-200 bg-white p-6">
        <h2 className="mb-4 text-lg font-semibold">New claim</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium" htmlFor="claim-files">
              Claim documents (JPG, PNG, PDF)
            </label>
            <input
              id="claim-files"
              type="file"
              multiple
              accept=".jpg,.jpeg,.png,.pdf"
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              className="text-sm"
            />
          </div>
          {files.length > 0 && (
            <ul className="list-inside list-disc text-sm text-gray-600">
              {files.map((file) => (
                <li key={file.name}>
                  {file.name} ({(file.size / 1024).toFixed(1)} KB)
                </li>
              ))}
            </ul>
          )}
          {files.length === 0 && (
            <p className="text-sm text-gray-500">
              No files selected — this creates an empty claim; you can upload files from
              the review page.
            </p>
          )}
          {error && (
            <p role="alert" className="text-sm text-red-700">
              {error}
            </p>
          )}
          {error && createdId && (
            <p className="text-sm">
              The claim was created before the upload failed:{" "}
              <Link to={`/claims/${createdId}`} className="text-blue-600 underline">
                open it to retry
              </Link>
            </p>
          )}
          <button
            type="submit"
            disabled={submit.isPending}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {submit.isPending
              ? "Working…"
              : files.length > 0
                ? "Create claim and upload"
                : "Create claim"}
          </button>
        </form>
      </section>

      <section>
        <h2 className="mb-4 text-lg font-semibold">Recent claims</h2>
        {claimsQuery.isPending && <p className="text-sm text-gray-500">Loading…</p>}
        {claimsQuery.isError && (
          <p className="text-sm text-red-700">Could not load claims. Is the backend running?</p>
        )}
        {claimsQuery.data?.length === 0 && (
          <p className="text-sm text-gray-500">No claims yet.</p>
        )}
        <ul className="space-y-2">
          {claimsQuery.data?.map((claim) => (
            <li key={claim.id} className="rounded border border-gray-200 bg-white px-4 py-3">
              <Link to={`/claims/${claim.id}`} className="text-sm font-medium text-blue-600 underline">
                {claim.id}
              </Link>
              <span
                className={`ml-3 rounded px-2 py-0.5 text-xs ${STATUS_STYLES[claim.extraction_status] ?? ""}`}
              >
                {claim.extraction_status}
              </span>
              {claim.triage_route ? (
                <span
                  className={`ml-3 rounded px-2 py-0.5 text-xs ${ROUTE_STYLES[claim.triage_route] ?? ""}`}
                >
                  {ROUTE_LABELS[claim.triage_route]}
                </span>
              ) : (
                <span className="ml-3 text-xs text-gray-500">Not triaged</span>
              )}
              <span className="ml-3 text-sm text-gray-500">
                {claim.claimant_name ?? "—"} · {claim.policy_number ?? "—"} ·{" "}
                {claim.file_count} file{claim.file_count === 1 ? "" : "s"} ·{" "}
                {new Date(claim.created_at).toLocaleString()}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
