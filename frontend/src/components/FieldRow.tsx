import { LOW_CONFIDENCE, type FieldDef } from "../fields";
import type { ExtractedClaim, FieldProvenance } from "../api";

interface Props {
  def: FieldDef;
  field: FieldProvenance;
  onChange: (value: string | number | null) => void;
  onSourceClick?: () => void;
}

export default function FieldRow({ def, field, onChange, onSourceClick }: Props) {
  const low = field.confidence < LOW_CONFIDENCE;
  const inputId = `field-${def.key}`;
  const inputClass =
    "w-full rounded border px-2 py-1 text-sm " +
    (low ? "border-amber-400 bg-amber-50" : "border-gray-300 bg-white");
  const inputValue = field.value ?? "";

  const handleText = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    onChange(e.target.value === "" ? null : e.target.value);
  };

  return (
    <div className={`rounded border px-3 py-2 ${low ? "border-amber-300" : "border-gray-200"}`}>
      <div className="mb-1 flex items-center justify-between">
        <label className="text-sm font-medium" htmlFor={inputId}>
          {def.label}
        </label>
        <span
          className={`text-xs ${low ? "font-semibold text-amber-700" : "text-gray-500"}`}
          title={field.source_file ? `From ${field.source_file}` : "No source file"}
        >
          confidence {field.confidence.toFixed(2)}
          {low ? " · low" : ""}
          {field.source_file && onSourceClick ? (
            <>
              {" · "}
              <button
                type="button"
                onClick={onSourceClick}
                className="text-blue-600 underline"
              >
                {field.source_file}
              </button>
            </>
          ) : (
            field.source_file ? ` · ${field.source_file}` : ""
          )}
        </span>
      </div>
      {def.kind === "textarea" ? (
        <textarea
          id={inputId}
          className={inputClass}
          rows={2}
          value={typeof inputValue === "string" ? inputValue : String(inputValue)}
          onChange={handleText}
        />
      ) : def.kind === "select" ? (
        <select
          id={inputId}
          className={inputClass}
          value={typeof inputValue === "string" ? inputValue : ""}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        >
          <option value="">Unknown</option>
          {def.options?.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : def.kind === "number" ? (
        <input
          id={inputId}
          className={inputClass}
          type="number"
          step="0.01"
          min="0"
          value={typeof inputValue === "number" ? inputValue : ""}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? null : Number(raw));
          }}
        />
      ) : (
        <input
          id={inputId}
          className={inputClass}
          type={def.kind === "date" ? "date" : "text"}
          value={typeof inputValue === "string" ? inputValue : String(inputValue)}
          onChange={handleText}
        />
      )}
    </div>
  );
}

export function withValue(
  claim: ExtractedClaim,
  key: keyof ExtractedClaim,
  value: string | number | null,
): ExtractedClaim {
  return { ...claim, [key]: { ...claim[key], value } };
}
