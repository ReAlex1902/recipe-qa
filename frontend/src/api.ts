// Types mirror SPEC.md §3 / evals/response.schema.json.

export type RefusalReason = "out_of_corpus" | "out_of_domain" | "safety";

export interface Citation {
  recipe_id: string;
  title: string;
  url: string;
}

export interface AllergyMatch {
  recipe_id: string;
  allergen: string;
  ingredient: string;
}

export interface AskResponse {
  answer: string | null;
  citations: Citation[];
  refused: boolean;
  refusal_reason: RefusalReason | null;
  safety_notice: string | null;
  allergy_check: { allergens: string[]; matches: AllergyMatch[] } | null;
  constraints: {
    max_total_minutes: number | null;
    diet: "vegetarian" | "vegan" | null;
    exclude_ingredients: string[];
  };
  request_id: string;
}

export interface ApiError {
  error: { code: string; message: string };
  request_id: string;
}

export type AskResult =
  | { ok: true; data: AskResponse }
  | { ok: false; status: number; error: ApiError | null };

export async function ask(question: string, signal?: AbortSignal): Promise<AskResult> {
  const resp = await fetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
    signal,
  });
  const body: unknown = await resp.json().catch(() => null);
  if (resp.ok) return { ok: true, data: body as AskResponse };
  return { ok: false, status: resp.status, error: (body as ApiError | null) ?? null };
}
