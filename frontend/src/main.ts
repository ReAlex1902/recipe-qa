import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./style.css";

import { ask, type AskResponse, type AskResult, type RefusalReason } from "./api";

const REFUSAL_LABELS: Record<RefusalReason, string> = {
  out_of_corpus: "Not in the recipe collection",
  out_of_domain: "Not a recipe question",
  safety: "Declined for safety",
};

const ERROR_MESSAGES: Record<string, string> = {
  invalid_request: "Please enter a question of 1 to 500 characters.",
  rate_limited: "Too many questions in a minute. Wait a moment and try again.",
  budget_exhausted: "Today's question budget is used up. Please try again tomorrow.",
  upstream_unavailable: "The language model is not available right now. Please try again.",
};

const ALLERGEN_LABELS: Record<string, string> = {
  tree_nut: "Tree nuts",
  peanut: "Peanuts",
  dairy: "Dairy",
  egg: "Egg",
  gluten: "Gluten",
  fish: "Fish",
  shellfish: "Shellfish",
  soy: "Soy",
  sesame: "Sesame",
};

const form = document.querySelector<HTMLFormElement>("#ask-form")!;
const input = document.querySelector<HTMLTextAreaElement>("#question")!;
const submit = document.querySelector<HTMLButtonElement>("#submit")!;
const counter = document.querySelector<HTMLSpanElement>("#counter")!;
const result = document.querySelector<HTMLElement>("#result")!;

// DOM is built with textContent only: model output is never parsed as HTML.
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

function icon(name: string): HTMLSpanElement {
  const span = el("span", { className: `icon icon-${name}` });
  span.setAttribute("aria-hidden", "true");
  return span;
}

/** Plain-text answer to paragraphs and lists ("- item", "1. step"), still textContent only. */
function renderProse(text: string): HTMLElement {
  const root = el("div", { className: "prose" });
  let list: HTMLUListElement | HTMLOListElement | null = null;
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (paragraph.length) root.append(el("p", { text: paragraph.join(" ") }));
    paragraph = [];
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushParagraph();
      const kind = bullet ? "UL" : "OL";
      if (!list || list.tagName !== kind) {
        list = bullet ? el("ul") : el("ol");
        root.append(list);
      }
      list.append(el("li", { text: (bullet ?? numbered)![1] }));
    } else if (line === "") {
      flushParagraph();
      list = null;
    } else {
      list = null;
      paragraph.push(line);
    }
  }
  flushParagraph();
  return root;
}

function sourceLink(url: string, title: string): HTMLElement {
  // Citations come from the corpus; only Wikibooks links become anchors.
  const label = el("span", {}, document.createTextNode(title), el("small", { text: "en.wikibooks.org" }));
  if (!url.startsWith("https://en.wikibooks.org/")) return el("div", { className: "source" }, label);
  const a = el("a", { className: "source" }, label, icon("arrow"));
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function metaLine(data: AskResponse): HTMLElement {
  const c = data.constraints;
  const filters = [
    c.max_total_minutes !== null ? `max ${c.max_total_minutes} min` : null,
    c.diet,
    ...c.exclude_ingredients.map((i) => `without ${i}`),
  ].filter((v): v is string => Boolean(v));
  const meta = el("div", { className: "meta" });
  if (filters.length) meta.append(el("span", { text: `Filters: ${filters.join(", ")}` }));
  meta.append(el("span", { className: "mono", text: `request ${data.request_id}` }));
  return meta;
}

function renderResponse(question: string, data: AskResponse): HTMLElement {
  const card = el("article", { className: "answer" });
  card.append(el("p", { className: "question-echo", text: question }));

  if (data.refused && data.refusal_reason) {
    const status = el("span", { className: "status" }, icon("prohibit"));
    status.append(document.createTextNode(REFUSAL_LABELS[data.refusal_reason]));
    card.append(status);
  }
  card.append(renderProse(data.answer ?? ""));

  if (data.safety_notice) {
    const notice = el("div", { className: "notice" }, icon("warning"));
    notice.append(el("strong", { text: "Check before cooking" }));
    notice.append(el("p", { text: data.safety_notice }));
    const matches = data.allergy_check?.matches ?? [];
    if (matches.length) {
      const list = el("ul");
      for (const m of matches) {
        list.append(el("li", { text: `${ALLERGEN_LABELS[m.allergen] ?? m.allergen}: ${m.ingredient}` }));
      }
      notice.append(list);
    }
    card.append(notice);
  }

  if (data.citations.length) {
    const sources = el("section", { className: "sources" });
    sources.append(el("h3", { className: "section-title", text: data.citations.length > 1 ? "Sources" : "Source" }));
    const list = el("ol");
    for (const c of data.citations) list.append(el("li", {}, sourceLink(c.url, c.title)));
    sources.append(list);
    card.append(sources);
  }

  card.append(metaLine(data));
  return card;
}

function renderError(question: string, res: Extract<AskResult, { ok: false }> | null): HTMLElement {
  const code = res?.error?.error.code ?? "";
  const message = res
    ? ERROR_MESSAGES[code] ?? `Something went wrong (HTTP ${res.status}).`
    : "Network error. Check your connection and try again.";
  const card = el("article", { className: "answer error" });
  const status = el("span", { className: "status" }, icon("warning"));
  status.append(document.createTextNode("Could not answer"));
  card.append(status, el("p", { className: "prose", text: message }));
  if (code !== "invalid_request") {
    const retry = el("button", { className: "retry" }, icon("retry"));
    retry.type = "button";
    retry.append(document.createTextNode("Try again"));
    retry.addEventListener("click", () => void submitQuestion(question));
    card.append(retry);
  }
  if (res?.error?.request_id) {
    card.append(el("div", { className: "meta" }, el("span", { className: "mono", text: `request ${res.error.request_id}` })));
  }
  return card;
}

function renderLoading(question: string): HTMLElement {
  const card = el("article", { className: "answer" });
  card.append(el("p", { className: "question-echo", text: question }));
  const skeleton = el("div", { className: "skeleton" });
  for (const w of ["w-90", "w-75", "w-90", "w-60", "w-40"]) skeleton.append(el("div", { className: `bar ${w}` }));
  card.append(skeleton);
  return card;
}

let inFlight: AbortController | null = null;

/** Keep the current question in the address bar (?q=...) so an answer can be shared. */
function shareableUrl(question: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("q", question);
  window.history.replaceState(null, "", url);
}

async function submitQuestion(question: string): Promise<void> {
  if (!question) {
    input.focus();
    return;
  }
  shareableUrl(question);
  inFlight?.abort();
  const controller = new AbortController();
  inFlight = controller;
  submit.disabled = true;
  result.setAttribute("aria-busy", "true");
  result.replaceChildren(renderLoading(question));
  try {
    const res = await ask(question, controller.signal);
    result.replaceChildren(res.ok ? renderResponse(question, res.data) : renderError(question, res));
  } catch (err) {
    if ((err as Error).name !== "AbortError") result.replaceChildren(renderError(question, null));
  } finally {
    if (inFlight === controller) {
      submit.disabled = false;
      result.setAttribute("aria-busy", "false");
    }
  }
}

function updateCounter(): void {
  counter.textContent = `${input.value.length}/500`;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  void submitQuestion(input.value.trim());
});

input.addEventListener("input", updateCounter);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

for (const button of document.querySelectorAll<HTMLButtonElement>(".chip")) {
  button.addEventListener("click", () => {
    input.value = button.textContent ?? "";
    updateCounter();
    form.requestSubmit();
  });
}

// A link like /?q=How+do+I+make+carbonara%3F asks the question on load.
const linked = new URLSearchParams(window.location.search).get("q")?.trim().slice(0, 500);
if (linked) {
  input.value = linked;
  updateCounter();
  void submitQuestion(linked);
}
