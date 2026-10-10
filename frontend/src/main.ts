import { ask, type AskResponse, type AskResult, type RefusalReason } from "./api";

const REFUSAL_LABELS: Record<RefusalReason, string> = {
  out_of_corpus: "Not in the recipe collection",
  out_of_domain: "Not a recipe question",
  safety: "Safety",
};

const ERROR_MESSAGES: Record<string, string> = {
  invalid_request: "Please enter a question (1–500 characters).",
  rate_limited: "Too many questions in a minute. Please wait a moment.",
  budget_exhausted: "The daily question budget is used up. Please try again tomorrow.",
  upstream_unavailable: "The language model is not available right now. Please try again.",
};

const form = document.querySelector<HTMLFormElement>("#ask-form")!;
const input = document.querySelector<HTMLTextAreaElement>("#question")!;
const submit = document.querySelector<HTMLButtonElement>("#submit")!;
const counter = document.querySelector<HTMLSpanElement>("#counter")!;
const result = document.querySelector<HTMLElement>("#result")!;

// Build DOM with textContent only: model output is never parsed as HTML.
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: { className?: string; text?: string } = {},
  ...children: Node[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (props.className) node.className = props.className;
  if (props.text !== undefined) node.textContent = props.text;
  node.append(...children);
  return node;
}

function safeLink(url: string, text: string): Node {
  // Citations come from the corpus; still, only Wikibooks links become anchors.
  if (!url.startsWith("https://en.wikibooks.org/")) return document.createTextNode(text);
  const a = el("a", { text });
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function renderResponse(data: AskResponse): HTMLElement {
  const card = el("div", { className: data.refused ? "card refused" : "card" });
  if (data.refused && data.refusal_reason) {
    card.append(el("span", { className: "badge", text: `Refused · ${REFUSAL_LABELS[data.refusal_reason]}` }));
  }
  card.append(el("div", { className: "answer", text: data.answer ?? "" }));

  if (data.safety_notice) {
    const notice = el("div", { className: "notice" }, el("strong", { text: "Allergy notice: " }));
    notice.append(document.createTextNode(data.safety_notice));
    const matches = data.allergy_check?.matches ?? [];
    if (matches.length) {
      const list = el("ul");
      for (const m of matches) list.append(el("li", { text: `${m.allergen}: “${m.ingredient}” (${m.recipe_id})` }));
      notice.append(el("div", { text: "Listed ingredients that match:" }), list);
    }
    card.append(notice);
  }

  if (data.citations.length) {
    const list = el("ul");
    for (const c of data.citations) list.append(el("li", {}, safeLink(c.url, c.title)));
    card.append(el("h3", { text: "Sources" }), list);
  }

  const c = data.constraints;
  const applied = [
    c.max_total_minutes !== null ? `≤ ${c.max_total_minutes} min` : null,
    c.diet,
    ...c.exclude_ingredients.map((i) => `no ${i}`),
  ].filter(Boolean);
  const meta = applied.length ? `Filters applied: ${applied.join(", ")} · ` : "";
  card.append(el("div", { className: "meta", text: `${meta}request ${data.request_id}` }));
  return card;
}

function renderResult(res: AskResult): HTMLElement {
  if (res.ok) return renderResponse(res.data);
  const code = res.error?.error.code ?? "";
  const message = ERROR_MESSAGES[code] ?? `Something went wrong (HTTP ${res.status}).`;
  const card = el("div", { className: "card error" }, el("div", { text: message }));
  if (res.error?.request_id) card.append(el("div", { className: "meta", text: `request ${res.error.request_id}` }));
  return card;
}

let inFlight: AbortController | null = null;

async function submitQuestion(question: string): Promise<void> {
  inFlight?.abort();
  inFlight = new AbortController();
  submit.disabled = true;
  submit.textContent = "Thinking…";
  result.replaceChildren(el("p", { className: "hint", text: "Searching the recipes…" }));
  try {
    result.replaceChildren(renderResult(await ask(question, inFlight.signal)));
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      result.replaceChildren(el("div", { className: "card error", text: "Network error. Is the service running?" }));
    }
  } finally {
    submit.disabled = false;
    submit.textContent = "Ask";
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitQuestion(input.value.trim());
});

input.addEventListener("input", () => {
  counter.textContent = `${input.value.length} / 500`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

for (const button of document.querySelectorAll<HTMLButtonElement>(".example")) {
  button.addEventListener("click", () => {
    input.value = button.textContent ?? "";
    counter.textContent = `${input.value.length} / 500`;
    form.requestSubmit();
  });
}
