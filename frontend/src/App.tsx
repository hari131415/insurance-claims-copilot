import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router-dom";
import { getHealth } from "./api";
import ReviewPage from "./pages/ReviewPage";
import UploadPage from "./pages/UploadPage";

const BADGE_LABELS: Record<string, string> = {
  mock: "Demo mode – mock provider",
  recorded: "Demo mode – recorded Claude results",
};

export default function App() {
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const badge = healthQuery.data
    ? BADGE_LABELS[healthQuery.data.llm_provider]
    : undefined;

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-lg font-semibold">
            Claims Intake Copilot
          </Link>
          <span className="flex items-center gap-3">
            {badge && (
              <span
                role="status"
                aria-label={badge}
                className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
              >
                {badge}
              </span>
            )}
            <span className="text-sm text-gray-500">Human review first pass</span>
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/claims/:id" element={<ReviewPage />} />
          <Route
            path="*"
            element={
              <p>
                Page not found. <Link to="/" className="text-blue-600 underline">Back home</Link>
              </p>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
