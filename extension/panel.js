/* TAYO side-panel controller.
 * All network calls go to the local backend. No third-party scripts.
 */

const extensionApi = globalThis.browser || globalThis.chrome;

const DEFAULT_BACKEND = "http://127.0.0.1:8000";
const MAX_TIMEOUT = 1000 * 60 * 10;

const state = {
  backendUrl: DEFAULT_BACKEND,
  activeTab: "factcheck",
  imageFile: null,
  imageObjectUrl: null,
  videoFile: null,
  lastHandled: 0,
  lastClaim: "",
  editorialSources: [],
};

const hasExtensionApi = Boolean(extensionApi?.storage);

/* ---------------- Tiny DOM helpers ---------------- */

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function clear(node) {
  node.replaceChildren();
  return node;
}

function pct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "n/a";
  }
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function languageLabel(language) {
  return {
    mn_cyrl: "Монгол кирилл",
    mn_latn: "Монгол латин галиг",
  }[language] || null;
}

function fmtBytes(bytes) {
  if (!bytes && bytes !== 0) return "n/a";
  const units = ["B", "KB", "MB", "GB"];
  let value = Number(bytes);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function fmtDuration(seconds) {
  const total = Number(seconds);
  if (!Number.isFinite(total)) return "n/a";
  const mins = Math.floor(total / 60);
  const secs = Math.round(total % 60);
  return mins ? `${mins}m ${secs}s` : `${secs.toFixed(1)}s`;
}

function shortHash(hash) {
  if (!hash || typeof hash !== "string") return "n/a";
  return `${hash.slice(0, 12)}…${hash.slice(-8)}`;
}

function badgeFor(label) {
  const value = String(label || "").toLowerCase();
  let cls = "badge-unsure";
  if (value.includes("not ai") || value.includes("human") || value.includes("authentic")) {
    cls = "badge-real";
  } else if (value.includes("possibly") || value.includes("uncertain")) {
    cls = "badge-unsure";
  } else if (value.includes("likely ai") || value.includes("synthetic") || value.includes("fake")) {
    cls = "badge-high";
  } else if (value === "true") {
    cls = "badge-true";
  } else if (value === "false") {
    cls = "badge-false";
  } else if (value === "not sure") {
    cls = "badge-unsure";
  } else if (value.includes("low risk")) {
    cls = "badge-low";
  } else if (value.includes("medium")) {
    cls = "badge-medium";
  } else if (value.includes("high")) {
    cls = "badge-high";
  }
  return el("span", `badge ${cls}`, label);
}

function addActionRow(card, data) {
  const row = el("div", "result-actions");
  const copy = el("button", "small-button", "Copy JSON");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      copy.textContent = "Copied";
      setTimeout(() => {
        copy.textContent = "Copy JSON";
      }, 1400);
    } catch {
      copy.textContent = "Copy failed";
    }
  });
  row.append(copy);
  card.append(row);
}

function resultCard(title, extraClass) {
  const card = el("section", `result-card${extraClass ? ` ${extraClass}` : ""}`);
  if (title) card.append(el("h3", null, title));
  return card;
}

function makeDetails(title, bodyNode, open = false) {
  const details = el("details");
  details.open = open;
  const summary = el("summary", null, title);
  details.append(summary, bodyNode);
  return details;
}

function detailsBody(node) {
  const body = el("div", "details-body");
  body.append(node);
  return body;
}

function addLimitations(container, limitations, action) {
  if (Array.isArray(limitations) && limitations.length) {
    const list = el("ul");
    limitations.forEach((item) => list.append(el("li", null, item)));
    container.append(makeDetails("Limitations", detailsBody(list)));
  }
  if (action) {
    const card = resultCard("What to do next");
    card.append(el("p", "summary", action));
    container.append(card);
  }
}

function renderError(container, error) {
  clear(container);
  const card = el("div", "error-card");
  card.append(el("strong", null, "Analysis failed"));
  card.append(el("span", null, error?.message || String(error)));
  card.append(
    el(
      "p",
      "inline-note",
      "If the model is downloading for the first time, wait a minute and try again. Check the backend terminal for details.",
    ),
  );
  container.append(card);
}

function renderLoading(container, message) {
  clear(container);
  const card = el("div", "loading-card");
  card.append(el("span", "spinner"), el("span", null, message));
  container.append(card);
}

/* ---------------- API ---------------- */

async function api(path, options = {}) {
  const { method = "GET", body, form, timeout = 120000 } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.min(timeout, MAX_TIMEOUT));
  const headers = {};
  let payload;
  if (form) {
    payload = form;
  } else if (body !== undefined) {
    payload = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }

  try {
    const response = await fetch(`${state.backendUrl}${path}`, {
      method,
      headers,
      body: payload,
      signal: controller.signal,
    });
    const text = await response.text();
    let data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { detail: text };
      }
    }
    if (!response.ok) {
      let detail = data.detail;
      if (Array.isArray(detail)) {
        detail = detail.map((item) => item.msg || String(item)).join("; ");
      }
      throw new Error(detail || `Request failed with HTTP ${response.status}.`);
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("The request timed out. The model may still be loading; try again.");
    }
    if (error instanceof TypeError) {
      throw new Error(
        `Could not reach the local server at ${state.backendUrl}. ` +
          "Start it with ./scripts/run_backend.sh, then check Settings.",
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function checkHealth(announce = false) {
  const status = document.getElementById("server-status");
  const text = document.getElementById("server-status-text");
  status.classList.remove("ok", "down");
  status.classList.add("checking");
  text.textContent = "Checking local server…";

  try {
    const health = await api("/api/health", { timeout: 5000 });
    status.classList.remove("checking");
    status.classList.add("ok");
    const loaded = health.models?.image_loaded?.length || 0;
    const device = health.models?.device || "cpu";
    text.textContent = `Local server ready · ${device.toUpperCase()}${
      loaded ? ` · ${loaded} image model${loaded > 1 ? "s" : ""}` : " · models load on demand"
    }`;
    if (announce) text.textContent += " · reconnected";
    return health;
  } catch (error) {
    status.classList.remove("checking");
    status.classList.add("down");
    text.textContent = "Local server offline · start ./scripts/run_backend.sh";
    if (announce) text.title = error.message;
    return null;
  }
}

/* ---------------- Settings ---------------- */

async function loadSettings() {
  let stored = {};
  if (hasExtensionApi && extensionApi.storage.local) {
    stored = await extensionApi.storage.local.get({ backendUrl: DEFAULT_BACKEND });
  } else {
    try {
      stored = { backendUrl: localStorage.getItem("tayoBackend") || DEFAULT_BACKEND };
    } catch {
      stored = { backendUrl: DEFAULT_BACKEND };
    }
  }
  state.backendUrl = (stored.backendUrl || DEFAULT_BACKEND).replace(/\/+$/, "");
  document.getElementById("backend-url").value = state.backendUrl;
}

async function saveSettings(url) {
  state.backendUrl = url.replace(/\/+$/, "");
  if (hasExtensionApi && extensionApi.storage.local) {
    await extensionApi.storage.local.set({ backendUrl: state.backendUrl });
  } else {
    try {
      localStorage.setItem("tayoBackend", state.backendUrl);
    } catch {
      /* ignore */
    }
  }
}

/* ---------------- Tabs ---------------- */

function switchTab(name) {
  if (!document.getElementById(`panel-${name}`)) return;
  state.activeTab = name;
  document.querySelectorAll(".tab").forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === `panel-${name}`);
  });
}

/* ---------------- Fact-check ---------------- */

function renderEvidence(evidence) {
  const list = el("div", "evidence-list");
  (evidence || []).forEach((item) => {
    const url = typeof item.url === "string" ? item.url : "";
    const isLink = /^https?:\/\//i.test(url);
    const node = el(isLink ? "a" : "div", "evidence-item");
    if (isLink) {
      node.href = url;
      node.target = "_blank";
      node.rel = "noreferrer noopener";
    }
    node.append(el("span", "evidence-source", item.source || "Source"));
    node.append(el("span", "evidence-title", item.title || item.url || "Untitled evidence"));
    if (item.snippet) node.append(el("span", "evidence-snippet", item.snippet));
    const reviewDate = item.review_date || item.published;
    if (reviewDate) node.append(el("span", "evidence-snippet", `Published/reviewed: ${reviewDate}`));
    if (item.published_verdict) {
      node.append(el("span", "evidence-snippet", `This publisher's assessment: ${item.published_verdict}`));
    }
    if (item.matched_claim) {
      node.append(el("span", "evidence-snippet", `Claim reviewed: ${item.matched_claim}`));
    }
    if (Number.isFinite(item.match_score)) {
      node.append(el("span", "evidence-snippet", `Text similarity: ${pct(item.match_score)}`));
    }
    if (item.relation && item.relation !== "supports") {
      const relation = el(
        "span",
        "evidence-snippet",
        item.relation === "contradicts"
          ? "Contradicts the claim"
          : item.relation === "search"
            ? "Search trusted fact-checkers"
            : item.relation === "fact_check"
              ? "Published fact-check; read the article"
              : item.relation === "related"
                ? "Related article; no verdict detected"
            : item.relation,
      );
      relation.style.fontStyle = "italic";
      node.append(relation);
    }
    list.append(node);
  });
  return list;
}

function renderFactcheck(container, data) {
  clear(container);
  const card = resultCard();
  const head = el("div", "score-head");
  head.append(badgeFor(data.verdict || "Not Sure"));
  const hasCuratedVerdict = String(data.method || "").startsWith("curated_corpus");
  head.append(
    el(
      "span",
      "score-sub",
      hasCuratedVerdict ? `Corpus match confidence ${pct(data.confidence)}` : "No independent verdict",
    ),
  );
  card.append(head);
  card.append(el("p", "summary", data.summary || "No summary returned."));
  if (data.matched_claim) {
    card.append(
      el(
        "p",
        "match-line",
        `${hasCuratedVerdict ? "Matched curated claim" : "Publisher-reviewed claim"}: “${data.matched_claim}” · text similarity ${pct(data.match_score)}`,
      ),
    );
  }
  const meta = el("div", "meta-line");
  const methodLabel =
    {
      curated_corpus: "Curated corpus",
      google_factcheck_tools: "Google Fact Check Tools",
      indexed_evidence: "Mongolian publisher article index",
      "curated_corpus+indexed_evidence": "Curated match + publisher articles",
      "google_factcheck_tools+indexed_evidence": "Google Fact Check + publisher articles",
      no_match: "No confident match",
    }[data.method] || data.method || "unknown";
  meta.append(el("span", null, `Method: ${methodLabel}`));
  if (languageLabel(data.language)) {
    meta.append(el("span", null, `Бичиг: ${languageLabel(data.language)}`));
  }
  if (data.topic) meta.append(el("span", null, `Topic: ${data.topic}`));
  if (data.corpus_reviewed_at) {
    meta.append(el("span", null, `Curated evidence reviewed: ${data.corpus_reviewed_at}`));
  }
  if (Number.isFinite(data.indexed_source_count)) {
    meta.append(el("span", null, `Indexed publishers with related articles: ${data.indexed_source_count}`));
  }
  if (Number.isFinite(data.source_count)) {
    meta.append(el("span", null, `Publishers reviewing this same claim text: ${data.source_count}`));
  }
  if (Number.isFinite(data.claimreview_index?.records) && data.claimreview_index.records > 0) {
    meta.append(el("span", null, `Google ClaimReview local index: ${data.claimreview_index.records.toLocaleString()} records · imported ${data.claimreview_index.imported_at || "unknown"}`));
  }
  if (data.index_status?.available) {
    const publisherCount = Number.isFinite(data.index_status.publisher_count)
      ? ` across ${data.index_status.publisher_count} publishers`
      : "";
    meta.append(el("span", null, `Local article index: ${data.index_status.indexed_items} articles${publisherCount} · updated ${data.index_status.fetched_at || "unknown"}`));
  }
  if (data.index_status?.refreshing) {
    meta.append(el("span", null, "Эх сурвалжийн нийтлэлийн сан шинэчлэгдэж байна. Шинэ нийтлэл хайх бол хэсэг хүлээгээд дахин шалгана уу."));
  }
  if (Array.isArray(data.search_variants) && data.search_variants.length) {
    meta.append(el("span", null, `Google search spellings: ${data.search_variants.join(" · ")}`));
  }
  if (Array.isArray(data.index_search_variants) && data.index_search_variants.length) {
    meta.append(el("span", null, `Article index spellings: ${data.index_search_variants.join(" · ")}`));
  }
  if (data.summary_source) meta.append(el("span", null, `Summary: ${data.summary_source}`));
  card.append(meta);
  addActionRow(card, data);
  container.append(card);

  if (Array.isArray(data.evidence) && data.evidence.length) {
    const evidenceCard = resultCard(
      hasCuratedVerdict ? "Evidence & publisher articles" : "Publisher assessments & search leads",
    );
    evidenceCard.append(renderEvidence(data.evidence));
    container.append(evidenceCard);
  }

  if (Array.isArray(data.health_literature?.evidence) && data.health_literature.evidence.length) {
    const healthCard = resultCard("Biomedical literature leads");
    if (data.health_literature.note) {
      healthCard.append(el("p", "inline-note", data.health_literature.note));
    }
    if (Number.isFinite(data.health_literature.record_count)) {
      healthCard.append(el("p", "meta-line", `Matching records in search database: ${data.health_literature.record_count}`));
    }
    healthCard.append(renderEvidence(data.health_literature.evidence));
    container.append(healthCard);
  }

  if (Array.isArray(data.claimreview_evidence) && data.claimreview_evidence.length) {
    const reviewCard = resultCard("International ClaimReview matches");
    reviewCard.append(el("p", "inline-note", "These are assessments recorded by the named publishers in the Google ClaimReview feed. Ratings are not combined into TAYO's verdict; read each original article and confirm it checks the same claim."));
    reviewCard.append(renderEvidence(data.claimreview_evidence));
    container.append(reviewCard);
  }

  addLimitations(container, data.limitations, data.recommended_action);
  renderJournalistAssessments(
    document.getElementById("journalist-assessments"),
    data.journalist_assessments || [],
    data.claim || state.lastClaim,
  );
}

function safeLink(url, label) {
  const node = el("a", null, label || url);
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return el("span", null, label || url);
    node.href = parsed.href;
  } catch {
    return el("span", null, label || url);
  }
  node.target = "_blank";
  node.rel = "noopener noreferrer";
  return node;
}

function renderJournalistAssessments(container, reviews, claim) {
  if (!container) return;
  clear(container);
  const card = resultCard("Journalist assessments");
  card.append(el("p", "inline-note", "These are individual, self-attributed editorial assessments. TAYO does not verify reviewer identity or combine ratings into its own verdict."));
  if (!reviews.length) {
    card.append(el("p", "summary", "No saved journalist assessment matches this exact claim yet."));
  } else {
    const counts = new Map();
    reviews.forEach((review) => counts.set(review.verdict, (counts.get(review.verdict) || 0) + 1));
    const distribution = [...counts.entries()].map(([verdict, count]) => `${verdict}: ${count}`).join(" · ");
    card.append(el("p", "match-line", `${reviews.length} saved assessment(s) · ${distribution}`));
    const list = el("div", "editorial-list");
    reviews.forEach((review) => {
      const entry = el("article", "editorial-entry");
      const head = el("div", "score-head");
      head.append(badgeFor(review.verdict));
      head.append(el("span", "score-sub", `${review.reviewer} · ${review.created_at || "date unknown"} · name not verified`));
      entry.append(head);
      entry.append(el("p", null, review.rationale));
      (review.sources || []).forEach((source) => {
        const href = source.homepage_url || `https://${source.domain}/`;
        entry.append(safeLink(href, `${source.name} (${source.domain})`));
      });
      (review.evidence_urls || []).forEach((url) => entry.append(safeLink(url)));
      list.append(entry);
    });
    card.append(list);
  }
  const add = el("button", "button secondary", "Add journalist assessment");
  add.type = "button";
  add.addEventListener("click", () => {
    switchTab("editorial");
    document.getElementById("editorial-claim").value = claim || "";
    document.getElementById("reviewer-name").focus();
  });
  card.append(add);
  container.append(card);
}

/* ---------------- Text ---------------- */

function statGrid(stats, language) {
  const grid = el("div", "stat-grid");
  const entries = [
    ["Words", stats.words],
    ["Sentences", stats.sentences],
    ["Characters", stats.characters],
    ["Avg sentence", `${stats.avg_sentence_words ?? "n/a"} words`],
    ["Sentence CV", stats.sentence_length_cv],
    ["Type–token ratio", stats.type_token_ratio],
    ["Trigram repetition", stats.trigram_repetition],
  ];
  if (!languageLabel(language)) entries.push(["AI-style phrases", stats.ai_phrase_hits]);
  entries.forEach(([label, value]) => {
    const stat = el("div", "stat");
    stat.append(el("span", "stat-label", label));
    stat.append(el("span", "stat-value", value ?? "n/a"));
    grid.append(stat);
  });
  return grid;
}

function signalList(signals) {
  const list = el("div", "signal-list");
  (signals || []).forEach((signal) => {
    const item = el("div", "signal");
    item.dataset.direction = signal.direction || "neutral";
    const head = el("div", "signal-head");
    head.append(el("span", null, signal.name || "signal"));
    head.append(el("span", "signal-weight", signal.weight || ""));
    item.append(head, el("p", null, signal.explanation || ""));
    list.append(item);
  });
  return list;
}

function renderText(container, data) {
  clear(container);
  const probability = data.ai_probability ?? data.heuristic_score;
  const hasScore = probability !== null && probability !== undefined;
  const card = resultCard();
  const head = el("div", "score-head");
  head.append(badgeFor(data.label));
  head.append(
    el(
      "span",
      "score-sub",
      data.ai_probability !== null && data.ai_probability !== undefined
        ? `AI-like ${pct(data.ai_probability)}`
        : hasScore ? `Heuristic ${pct(data.heuristic_score)}` : "AI score unavailable",
    ),
  );
  card.append(head);

  if (hasScore) {
    const meter = el("div", "meter");
    const bar = el("span");
    bar.style.width = `${Math.round(probability * 100)}%`;
    meter.append(bar);
    card.append(meter);
    const labels = el("div", "meter-label");
    labels.append(el("span", null, "Human-like"), el("span", null, "AI-like"));
    card.append(labels);
  }
  card.append(el("p", "summary", data.summary || ""));

  const meta = el("div", "meta-line");
  if (hasScore) {
    meta.append(el("span", null, `Model: ${data.model_used || "heuristic"}`));
    meta.append(el("span", null, `Confidence: ${pct(data.confidence)}`));
  }
  if (languageLabel(data.language)) {
    meta.append(el("span", null, `Бичиг: ${languageLabel(data.language)}`));
  }
  if (data.chunks?.length) meta.append(el("span", null, `${data.chunks.length} chunk(s)`));
  card.append(meta);
  addActionRow(card, data);
  container.append(card);

  const statsCard = resultCard("Style statistics");
  statsCard.append(statGrid(data.text_stats || {}, data.language));
  if (data.text_stats?.ai_phrases_found?.length) {
    statsCard.append(
      el("p", "inline-note", `Phrases found: ${data.text_stats.ai_phrases_found.join(", ")}`),
    );
  }
  container.append(statsCard);

  const signalsCard = resultCard("Signals");
  signalsCard.append(signalList(data.signals));
  container.append(signalsCard);

  if (data.chunks?.length) {
    const pre = el("pre", null, JSON.stringify(data.chunks, null, 2));
    container.append(makeDetails("Per-chunk scores", detailsBody(pre)));
  }
  if (data.model_error) {
    container.append(el("p", "inline-note", `Model note: ${data.model_error}`));
  }

  addLimitations(container, data.limitations, data.recommended_action);
}

/* ---------------- Image ---------------- */

function metadataGrid(metadata) {
  const grid = el("dl", "kv");
  const exif = metadata.exif || {};
  const add = (key, value) => {
    if (value === undefined || value === null || value === "") return;
    grid.append(el("dt", null, key));
    grid.append(el("dd", null, String(value)));
  };
  add("Format", metadata.format);
  add("Dimensions", metadata.width && metadata.height ? `${metadata.width} × ${metadata.height}` : null);
  add("Color mode", metadata.mode);
  add("EXIF present", metadata.exif_present ? "yes" : "no");
  add("Camera make", exif.Make);
  add("Camera model", exif.Model);
  add("Software", metadata.software || exif.Software);
  add("C2PA manifest", metadata.c2pa_present ? "detected (not verified)" : "not detected");
  add("AI software hints", (metadata.ai_software_hints || []).join(", "));
  add("Metadata flags", (metadata.metadata_flags || []).join(", "));
  return grid;
}

function renderImage(container, data) {
  clear(container);
  if (data.error) {
    renderError(container, new Error(data.error));
    return;
  }

  const card = resultCard();
  const head = el("div", "score-head");
  head.append(badgeFor(data.label));
  head.append(
    el(
      "span",
      "score-sub",
      data.ai_probability !== null && data.ai_probability !== undefined
        ? `AI-like ${pct(data.ai_probability)}`
        : "No classifier score",
    ),
  );
  card.append(head);
  const meter = el("div", "meter");
  const bar = el("span");
  bar.style.width = `${Math.round((data.ai_probability || 0) * 100)}%`;
  meter.append(bar);
  card.append(meter);
  card.append(el("p", "summary", data.summary || ""));
  const meta = el("div", "meta-line");
  meta.append(el("span", null, `Confidence: ${pct(data.confidence)}`));
  meta.append(el("span", null, `Size: ${fmtBytes(data.file_size_bytes)}`));
  meta.append(el("span", null, `SHA-256: ${shortHash(data.sha256)}`));
  card.append(meta);
  addActionRow(card, data);
  container.append(card);

  if (data.models?.length) {
    const modelsCard = resultCard("Classifier output");
    data.models.forEach((model) => {
      const row = el("div", "signal");
      row.dataset.direction = model.ai_probability >= 0.5 ? "AI-like" : "real-like";
      const rowHead = el("div", "signal-head");
      rowHead.append(el("span", null, model.model_id));
      rowHead.append(el("span", "signal-weight", `${pct(model.ai_probability)} AI-like`));
      row.append(rowHead);
      row.append(
        el(
          "p",
          null,
          `Predicted: ${model.predicted_label} · ${model.latency_ms} ms · ${model.device}`,
        ),
      );
      modelsCard.append(row);
    });
    container.append(modelsCard);
  }

  const metadataCard = resultCard("Metadata & provenance");
  metadataCard.append(metadataGrid(data.metadata || {}));
  if (data.metadata && Object.keys(data.metadata).length) {
    const pre = el("pre", null, JSON.stringify(data.metadata, null, 2));
    metadataCard.append(makeDetails("Raw metadata", detailsBody(pre)));
  }
  container.append(metadataCard);

  if (data.signals?.length) {
    const signalsCard = resultCard("Signals");
    signalsCard.append(signalList(data.signals));
    container.append(signalsCard);
  }

  addLimitations(container, data.limitations, data.recommended_action);
}

/* ---------------- Video ---------------- */

function renderVideo(container, data) {
  clear(container);
  const card = resultCard();
  const head = el("div", "score-head");
  head.append(badgeFor(data.label));
  head.append(
    el(
      "span",
      "score-sub",
      data.ai_probability !== null && data.ai_probability !== undefined
        ? `Mean AI-like ${pct(data.ai_probability)}`
        : "No frame score",
    ),
  );
  card.append(head);
  if (data.ai_probability !== null && data.ai_probability !== undefined) {
    const meter = el("div", "meter");
    const bar = el("span");
    bar.style.width = `${Math.round(data.ai_probability * 100)}%`;
    meter.append(bar);
    card.append(meter);
  }
  card.append(el("p", "summary", data.summary || ""));
  const meta = el("div", "meta-line");
  meta.append(el("span", null, `Frames: ${data.frames_analyzed}/${data.frames_requested}`));
  meta.append(el("span", null, `Duration: ${fmtDuration(data.duration_seconds)}`));
  meta.append(el("span", null, `Size: ${fmtBytes(data.file_size_bytes)}`));
  if (data.max_frame_probability !== null) {
    meta.append(el("span", null, `Max frame: ${pct(data.max_frame_probability)}`));
  }
  card.append(meta);
  addActionRow(card, data);
  container.append(card);

  if (data.frame_results?.length) {
    const frames = resultCard("Sampled frames");
    const list = el("div", "signal-list");
    data.frame_results.forEach((frame) => {
      const item = el("div", "signal");
      item.dataset.direction = frame.ai_probability >= 0.5 ? "AI-like" : "real-like";
      const row = el("div", "signal-head");
      row.append(el("span", null, `Frame ${frame.index + 1} · ${fmtDuration(frame.timestamp_seconds)}`));
      row.append(el("span", "signal-weight", pct(frame.ai_probability)));
      item.append(row);
      list.append(item);
    });
    frames.append(list);
    container.append(frames);
  }

  if (data.metadata) {
    const metaCard = resultCard("Video metadata");
    const pre = el("pre", null, JSON.stringify(data.metadata, null, 2));
    metaCard.append(pre);
    container.append(metaCard);
  }

  if (data.signals?.length) {
    const cardSignals = resultCard("Signals");
    cardSignals.append(signalList(data.signals));
    container.append(cardSignals);
  }

  if (data.frame_error) {
    container.append(el("p", "inline-note", `Frame extraction note: ${data.frame_error}`));
  }
  addLimitations(container, data.limitations, data.recommended_action);
}

/* ---------------- URL ---------------- */

function riskClass(level) {
  const value = String(level || "").toLowerCase();
  if (value.includes("high")) return "var(--red)";
  if (value.includes("medium")) return "var(--amber)";
  if (value.includes("low")) return "var(--green)";
  return "var(--muted)";
}

function addKeyValues(card, pairs) {
  const grid = el("dl", "kv");
  pairs.forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    const display = typeof value === "object" ? JSON.stringify(value) : String(value);
    grid.append(el("dt", null, key), el("dd", null, display));
  });
  card.append(grid);
}

function renderUrl(container, data) {
  clear(container);
  const card = resultCard();
  const risk = el("div", "risk-card");
  const gauge = el("div", "risk-gauge");
  gauge.style.setProperty("--score", String(data.risk_score ?? 0));
  gauge.style.setProperty("--risk-color", riskClass(data.risk_level));
  const gaugeText = el("span", null, String(data.risk_score ?? 0));
  gaugeText.append(el("small", null, "/100"));
  gauge.append(gaugeText);
  const copy = el("div", "risk-copy");
  copy.append(badgeFor(data.risk_level || "Unknown"));
  copy.append(el("p", "summary", data.summary || ""));
  risk.append(gauge, copy);
  card.append(risk);
  const meta = el("div", "meta-line");
  meta.append(el("span", null, `Host: ${data.host || "n/a"}`));
  meta.append(el("span", null, `Domain: ${data.registered_domain || "n/a"}`));
  card.append(meta);
  addActionRow(card, data);
  container.append(card);

  if (data.reasons?.length) {
    const reasonsCard = resultCard("Why this score");
    const list = el("ul", "reason-list");
    data.reasons.forEach((reason) => list.append(el("li", null, reason)));
    reasonsCard.append(list);
    container.append(reasonsCard);
  }

  const checks = data.checks || {};

  if (checks.offline?.length) {
    const list = el("div", "signal-list");
    checks.offline.forEach((signal) => {
      const item = el("div", "signal");
      item.dataset.direction = signal.triggered ? (signal.weight > 0 ? "risk" : "neutral") : "safe";
      const head = el("div", "signal-head");
      head.append(el("span", null, signal.name));
      head.append(
        el(
          "span",
          "signal-weight",
          signal.triggered ? (signal.weight > 0 ? `+${signal.weight}` : "ok") : "clear",
        ),
      );
      item.append(head, el("p", null, signal.detail));
      list.append(item);
    });
    const flagged = checks.offline.filter((signal) => signal.triggered && signal.weight > 0).length;
    container.append(
      makeDetails(
        `Offline URL heuristics (${checks.offline.length} checks, ${flagged} flagged)`,
        detailsBody(list),
      ),
    );
  }

  if (checks.dns) {
    const body = el("div");
    addKeyValues(body, [
      ["DNS status", checks.dns.status === 0 ? "NOERROR" : `status ${checks.dns.status}`],
      ["DNSSEC validated", checks.dns.dnssec?.validated ? "yes" : "no / not validated"],
      ["DS record", checks.dns.dnssec?.ds_present ? "present" : "absent"],
      ["A records", (checks.dns.answers?.A || []).map((a) => a.data).join(", ")],
      ["AAAA records", (checks.dns.answers?.AAAA || []).map((a) => a.data).join(", ")],
      ["NS records", (checks.dns.answers?.NS || []).map((a) => a.data).join(", ")],
      ["MX records", (checks.dns.answers?.MX || []).map((a) => a.data).join(", ")],
    ]);
    body.append(el("p", "inline-note", checks.dns.dnssec?.note || ""));
    container.append(makeDetails("DNS & DNSSEC", detailsBody(body)));
  }

  if (checks.tls) {
    const body = el("div");
    const cert = checks.tls.certificate || {};
    addKeyValues(body, [
      ["Connected", checks.tls.connected ? "yes" : "no"],
      ["Valid certificate", checks.tls.valid === true ? "yes" : checks.tls.valid === false ? "no" : "n/a"],
      ["TLS version", checks.tls.tls_version],
      ["Cipher", checks.tls.cipher],
      ["Subject", cert.subject],
      ["Issuer", cert.issuer],
      ["Validation type", cert.validation_type],
      ["Valid from", cert.not_before],
      ["Valid until", cert.not_after],
      ["Days until expiry", cert.days_until_expiry],
      ["SANs", (cert.san || []).join(", ")],
      ["Error", checks.tls.error],
    ]);
    body.append(
      el(
        "p",
        "inline-note",
        "A valid certificate only proves control of the domain, not that the site is trustworthy.",
      ),
    );
    container.append(makeDetails("TLS certificate", detailsBody(body)));
  }

  if (checks.rdap) {
    const body = el("div");
    const rdap = checks.rdap;
    addKeyValues(body, [
      ["Found", rdap.found ? "yes" : "no"],
      ["Registrar", rdap.registrar],
      ["Created", rdap.created],
      ["Expires", rdap.expires],
      ["Age (days)", rdap.age_days],
      ["Status", Array.isArray(rdap.status) ? rdap.status.join(", ") : rdap.status],
      ["Nameservers", Array.isArray(rdap.nameservers) ? rdap.nameservers.join(", ") : rdap.nameservers],
      ["Note", rdap.note],
      ["Error", rdap.error],
    ]);
    container.append(makeDetails("Registration data (RDAP)", detailsBody(body)));
  }

  const intel = checks.threat_intel || {};
  const intelCard = resultCard("Threat intelligence");
  addKeyValues(intelCard, [
    ["Google Safe Browsing", intel.safe_browsing?.matched ? "MATCH" : intel.safe_browsing?.enabled ? "no match" : intel.safe_browsing?.note],
    ["VirusTotal", intel.virustotal?.found === false ? intel.virustotal?.note : intel.virustotal?.stats ? `${intel.virustotal.stats.malicious || 0} malicious / ${intel.virustotal.stats.suspicious || 0} suspicious` : intel.virustotal?.note],
    ["URLhaus", intel.urlhaus?.query_status || intel.urlhaus?.note],
  ]);
  const intelRaw = el("pre", null, JSON.stringify(intel, null, 2));
  intelCard.append(makeDetails("Raw threat-intel output", detailsBody(intelRaw)));
  container.append(intelCard);

  addLimitations(container, data.limitations, data.recommended_action);
}

/* ---------------- Form wiring ---------------- */

async function runAnalysis({ containerId, button, loadingText, task, render }) {
  const container = document.getElementById(containerId);
  button.classList.add("is-loading");
  const wasDisabled = button.disabled;
  button.disabled = true;
  renderLoading(container, loadingText);
  try {
    const data = await task();
    render(container, data);
  } catch (error) {
    renderError(container, error);
  } finally {
    button.classList.remove("is-loading");
    button.disabled = wasDisabled;
  }
}

function wireFactcheck() {
  const form = document.getElementById("factcheck-form");
  const input = document.getElementById("claim-input");
  const language = document.getElementById("claim-language");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const claim = input.value.trim();
    if (claim.length < 3) {
      input.focus();
      return;
    }
    state.lastClaim = claim;
    const button = form.querySelector("button[type=submit]");
    await runAnalysis({
      containerId: "factcheck-output",
      button,
      loadingText: "Matching claim against trusted sources…",
      task: () => api("/api/factcheck", { method: "POST", body: { claim, language: language.value }, timeout: 90000 }),
      render: renderFactcheck,
    });
  });
}

const sourceTypeLabels = {
  fact_check: "Fact-check publisher",
  official: "Official / primary source",
  research: "Research database / literature",
  international: "International reference",
  news: "News publisher",
};

function renderEditorialSources(sources) {
  const container = document.getElementById("editorial-source-list");
  const select = document.getElementById("editorial-sources");
  if (!container || !select) return;
  clear(container);
  clear(select);
  state.editorialSources = sources || [];
  if (!state.editorialSources.length) {
    container.append(el("p", "inline-note", "No sources are registered yet."));
  }
  state.editorialSources.forEach((source) => {
    const option = el("option", null, `${source.name} — ${source.domain} (${sourceTypeLabels[source.tier] || source.tier})`);
    option.value = source.id;
    select.append(option);

    const entry = el("article", "editorial-entry");
    const heading = el("h4");
    heading.append(safeLink(source.homepage_url || `https://${source.domain}/`, source.name));
    entry.append(heading);
    entry.append(el("span", "editorial-status", source.verification_status || "registered; review status unknown"));
    entry.append(el("p", "meta-line", `${sourceTypeLabels[source.tier] || source.tier} · ${source.domain} · added by ${source.added_by || "unknown"}`));
    if (source.note) entry.append(el("p", null, source.note));
    if (source.verification_note) entry.append(el("p", null, `Review note: ${source.verification_note}`));
    const reviewSource = el("button", "small-button", "Mark journalist-reviewed");
    reviewSource.type = "button";
    reviewSource.addEventListener("click", async () => {
      const reviewer = window.prompt("Journalist who checked this website's scope, ownership, and corrections policy:");
      if (!reviewer?.trim()) return;
      const note = window.prompt("What did you verify? (optional)") || "";
      try {
        await api(`/api/editorial/sources/${encodeURIComponent(source.id)}/review`, {
          method: "POST", body: { reviewer: reviewer.trim(), note },
        });
        await loadEditorialSources();
      } catch (error) {
        document.getElementById("source-form-status").textContent = error.message;
      }
    });
    entry.append(reviewSource);
    container.append(entry);
  });
}

async function loadEditorialSources() {
  const container = document.getElementById("editorial-source-list");
  if (!container) return;
  const query = document.getElementById("source-search").value.trim();
  try {
    const data = await api(`/api/editorial/sources?q=${encodeURIComponent(query)}&limit=100`);
    renderEditorialSources(data.sources || []);
    const count = document.getElementById("source-count");
    count.textContent = data.total > 100
      ? `Showing 100 of ${data.total.toLocaleString()} sources. Search to narrow the list.`
      : `${data.total.toLocaleString()} saved sources.`;
  } catch (error) {
    clear(container).append(el("p", "error-card", error.message));
  }
}

function wireEditorial() {
  let searchTimer;
  document.getElementById("source-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadEditorialSources, 180);
  });

  document.getElementById("export-editorial-db").addEventListener("click", async () => {
    try {
      const data = await api("/api/editorial/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const objectUrl = URL.createObjectURL(blob);
      const link = el("a");
      link.href = objectUrl;
      link.download = "tayo-editorial-backup.json";
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      document.getElementById("source-form-status").textContent = error.message;
    }
  });

  const sourceForm = document.getElementById("source-form");
  const sourceStatus = document.getElementById("source-form-status");
  sourceForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    sourceStatus.textContent = "Saving source…";
    const body = Object.fromEntries(new FormData(sourceForm).entries());
    try {
      await api("/api/editorial/sources", { method: "POST", body });
      sourceStatus.textContent = "Saved. This source is pending editorial review.";
      sourceForm.reset();
      document.getElementById("source-search").value = "";
      await loadEditorialSources();
    } catch (error) {
      sourceStatus.textContent = error.message;
    }
  });

  const reviewForm = document.getElementById("editorial-review-form");
  const reviewStatus = document.getElementById("review-form-status");
  reviewForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    reviewStatus.textContent = "Saving assessment…";
    const body = Object.fromEntries(new FormData(reviewForm).entries());
    body.source_ids = [...document.getElementById("editorial-sources").selectedOptions].map((option) => option.value);
    body.evidence_urls = body.evidence_urls.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
    body.claim = body.claim.trim();
    try {
      const data = await api("/api/editorial/reviews", { method: "POST", body });
      reviewStatus.textContent = "Assessment saved. It will appear as a journalist's opinion, not TAYO's verdict.";
      const latest = await api(`/api/editorial/reviews?claim=${encodeURIComponent(body.claim)}`);
      renderJournalistAssessments(document.getElementById("journalist-assessments"), latest.reviews || [], body.claim);
      if (state.lastClaim && state.lastClaim === body.claim) {
        document.getElementById("factcheck-form").requestSubmit();
      }
      reviewForm.reset();
      document.getElementById("editorial-claim").value = body.claim;
      return data;
    } catch (error) {
      reviewStatus.textContent = error.message;
    }
  });
}

function wireText() {
  const form = document.getElementById("text-form");
  const input = document.getElementById("text-input");
  const language = document.getElementById("text-language");
  const counter = document.getElementById("text-count");
  const updateCount = () => {
    counter.textContent = `${input.value.length.toLocaleString()} characters`;
  };
  input.addEventListener("input", updateCount);
  updateCount();

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (text.length < 20) {
      input.focus();
      return;
    }
    const button = form.querySelector("button[type=submit]");
    await runAnalysis({
      containerId: "text-output",
      button,
      loadingText: "Running text classifier… first run may download ~300 MB.",
      task: () => api("/api/analyze/text", { method: "POST", body: { text, language: language.value }, timeout: 600000 }),
      render: renderText,
    });
  });
}

function setupDropzone(zone, input, onFile) {
  ["dragenter", "dragover"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("is-dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("is-dragover");
    });
  });
  zone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      input.files = event.dataTransfer.files;
      onFile(file);
    }
  });
  input.addEventListener("change", () => {
    if (input.files?.[0]) onFile(input.files[0]);
  });
}

function wireImage() {
  const form = document.getElementById("image-form");
  const urlForm = document.getElementById("image-url-form");
  const urlInput = document.getElementById("image-url-input");
  const fileInput = document.getElementById("image-file");
  const dropzone = document.getElementById("image-dropzone");
  const submit = document.getElementById("image-submit");
  const previewWrap = document.getElementById("image-preview-wrap");
  const preview = document.getElementById("image-preview");
  const clearButton = document.getElementById("image-clear");

  const resetFile = () => {
    state.imageFile = null;
    fileInput.value = "";
    submit.disabled = true;
    previewWrap.hidden = true;
    preview.removeAttribute("src");
    if (state.imageObjectUrl) {
      URL.revokeObjectURL(state.imageObjectUrl);
      state.imageObjectUrl = null;
    }
  };

  setupDropzone(dropzone, fileInput, (file) => {
    if (!file.type.startsWith("image/")) return;
    resetFile();
    state.imageFile = file;
    state.imageObjectUrl = URL.createObjectURL(file);
    preview.src = state.imageObjectUrl;
    previewWrap.hidden = false;
    submit.disabled = false;
  });

  clearButton.addEventListener("click", (event) => {
    event.preventDefault();
    resetFile();
  });

  urlForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = urlInput.value.trim();
    if (!url) return;
    await analyzeImageUrl(url);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.imageFile) return;
    const formData = new FormData();
    formData.append("file", state.imageFile, state.imageFile.name);
    await runAnalysis({
      containerId: "image-output",
      button: submit,
      loadingText: "Reading metadata and running the image classifier…",
      task: () =>
        api("/api/analyze/image", { method: "POST", form: formData, timeout: 600000 }),
      render: renderImage,
    });
  });
}

async function analyzeImageUrl(url, fallbackFrame = null) {
  await runAnalysis({
    containerId: "image-output",
    button: document.querySelector("#image-url-form button[type=submit]"),
    loadingText: "Fetching and inspecting the image…",
    task: async () => {
      try {
        return await api("/api/analyze/image-url", {
          method: "POST", body: { url }, timeout: 180000,
        });
      } catch (error) {
        if (!fallbackFrame) throw error;
        const data = await scoreVisibleImageFrame(fallbackFrame);
        data.image_fetch_error = error.message;
        return data;
      }
    },
    render: (container, data) => data.capture_scope === "visible_image_screenshot"
      ? renderVisibleImageFrame(container, data)
      : renderImage(container, data),
  });
}

async function scoreVisibleImageFrame(request) {
  const blob = await cropVisibleMediaFrame(request);
  const form = new FormData();
  form.append("file", blob, "visible-image.jpg");
  const data = await api("/api/analyze/image", {
    method: "POST", form, timeout: 600000,
  });
  data.capture_scope = "visible_image_screenshot";
  data.limitations = [
    "This is a screenshot of the displayed image. Original EXIF and C2PA metadata are unavailable.",
    ...(data.limitations || []),
  ];
  return data;
}

function renderVisibleImageFrame(container, data) {
  renderImage(container, data);
  const notice = el("div", "notice");
  notice.append(el("strong", null, "Displayed image only. "));
  notice.append(el("span", null,
    data.image_fetch_error ? `Original image download failed: ${data.image_fetch_error} ` : ""));
  notice.append(el("span", null,
    "TAYO inspected a screenshot. Original image metadata and full resolution were not available."));
  container.prepend(notice);
}

async function analyzeVisibleImageFrame(request) {
  await runAnalysis({
    containerId: "image-output",
    button: document.getElementById("image-submit"),
    loadingText: "Inspecting the displayed image…",
    task: () => scoreVisibleImageFrame(request),
    render: renderVisibleImageFrame,
  });
}

function wireVideo() {
  const form = document.getElementById("video-form");
  const urlForm = document.getElementById("video-url-form");
  const urlInput = document.getElementById("video-url-input");
  const fileInput = document.getElementById("video-file");
  const dropzone = document.getElementById("video-dropzone");
  const fileName = document.getElementById("video-file-name");
  const submit = document.getElementById("video-submit");

  urlForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = urlInput.value.trim();
    if (!url) return;
    await analyzeVideoUrl(url);
  });

  setupDropzone(dropzone, fileInput, (file) => {
    if (!file.type.startsWith("video/")) return;
    state.videoFile = file;
    fileName.textContent = `${file.name} · ${fmtBytes(file.size)}`;
    fileName.hidden = false;
    submit.disabled = false;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.videoFile) return;
    const formData = new FormData();
    formData.append("file", state.videoFile, state.videoFile.name);
    await runAnalysis({
      containerId: "video-output",
      button: submit,
      loadingText: "Extracting frames and scoring them… this can take a while on CPU.",
      task: () => api("/api/analyze/video", { method: "POST", form: formData, timeout: 900000 }),
      render: renderVideo,
    });
  });
}

async function analyzeVideoUrl(url, fallbackFrame = null) {
  await runAnalysis({
    containerId: "video-output",
    button: document.querySelector("#video-url-form button[type=submit]"),
    loadingText: "Fetching the public video and sampling its frames…",
    task: async () => {
      try {
        return await api("/api/analyze/video-url", {
          method: "POST", body: { url }, timeout: 600000,
        });
      } catch (error) {
        if (!fallbackFrame) throw error;
        const data = await scoreVisibleVideoFrame(fallbackFrame);
        data.video_fetch_error = error.message;
        return data;
      }
    },
    render: (container, data) => {
      if (data.capture_scope === "single_visible_video_frame") {
        renderVisibleVideoFrame(container, data);
      } else {
        renderVideo(container, data);
      }
    },
  });
}

async function cropVisibleMediaFrame(request) {
  const image = new Image();
  image.src = request.screenshot;
  await image.decode();
  const rect = request.rect;
  const left = Math.max(0, rect.left);
  const top = Math.max(0, rect.top);
  const right = Math.min(rect.viewportWidth, rect.left + rect.width);
  const bottom = Math.min(rect.viewportHeight, rect.top + rect.height);
  if (right - left < 32 || bottom - top < 32) {
    throw new Error("The media is not visible enough to inspect.");
  }
  const scaleX = image.naturalWidth / rect.viewportWidth;
  const scaleY = image.naturalHeight / rect.viewportHeight;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round((right - left) * scaleX);
  canvas.height = Math.round((bottom - top) * scaleY);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Could not prepare the captured media.");
  context.drawImage(
    image,
    left * scaleX, top * scaleY,
    (right - left) * scaleX, (bottom - top) * scaleY,
    0, 0, canvas.width, canvas.height,
  );
  return await new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("Could not encode the captured media.")),
      "image/jpeg", 0.88,
    );
  });
}

async function scoreVisibleVideoFrame(request) {
  const blob = await cropVisibleMediaFrame(request);
  const form = new FormData();
  form.append("file", blob, "visible-video-frame.jpg");
  const data = await api("/api/analyze/image", {
    method: "POST", form, timeout: 600000,
  });
  data.capture_scope = "single_visible_video_frame";
  data.limitations = [
    "Only one visible frame was inspected. This is not a video-wide result.",
    ...(data.limitations || []),
  ];
  return data;
}

function renderVisibleVideoFrame(container, data) {
  renderImage(container, data);
  const notice = el("div", "notice");
  notice.append(el("strong", null, "Current frame only. "));
  notice.append(el("span", null,
    data.video_fetch_error
      ? `Full video download failed: ${data.video_fetch_error} `
      : "This player has no direct video file URL. "));
  notice.append(el("span", null,
    "TAYO inspected one visible frame, not the full video. Upload the original file for multi-frame analysis."));
  container.prepend(notice);
}

async function analyzeVisibleVideoFrame(request) {
  await runAnalysis({
    containerId: "video-output",
    button: document.getElementById("video-submit"),
    loadingText: "Inspecting the visible video frame…",
    task: () => scoreVisibleVideoFrame(request),
    render: renderVisibleVideoFrame,
  });
}

function wireUrl() {
  const form = document.getElementById("url-form");
  const input = document.getElementById("url-input");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = input.value.trim();
    if (!url) return;
    const button = form.querySelector("button[type=submit]");
    await runAnalysis({
      containerId: "url-output",
      button,
      loadingText: "Running passive DNS, TLS, RDAP and threat-intel checks…",
      task: () => api("/api/scan/url", { method: "POST", body: { url }, timeout: 120000 }),
      render: renderUrl,
    });
  });
}

/* ---------------- Context-menu requests ---------------- */

async function handleExternalRequest(request) {
  if (!request || !request.type) return;
  if (Date.now() - (request.createdAt || 0) > 120000) return;
  if (Date.now() - state.lastHandled < 800) return;
  state.lastHandled = Date.now();

  if (request.type === "factcheck") {
    switchTab("factcheck");
    const input = document.getElementById("claim-input");
    input.value = request.value || "";
    document.getElementById("factcheck-form").requestSubmit();
  } else if (request.type === "url") {
    switchTab("url");
    document.getElementById("url-input").value = request.value || "";
    document.getElementById("url-form").requestSubmit();
  } else if (request.type === "image") {
    switchTab("image");
    document.getElementById("image-url-input").value = request.value || "";
    await analyzeImageUrl(request.value || "", request.frame);
  } else if (request.type === "image-frame") {
    switchTab("image");
    await analyzeVisibleImageFrame(request);
  } else if (request.type === "image-error") {
    switchTab("image");
    renderError(document.getElementById("image-output"), new Error(request.message));
  } else if (request.type === "text") {
    switchTab("text");
    const input = document.getElementById("text-input");
    input.value = request.value || "";
    input.dispatchEvent(new Event("input"));
    if (input.value.trim().length >= 20) {
      document.getElementById("text-form").requestSubmit();
    } else {
      renderError(document.getElementById("text-output"),
        new Error("Select at least 20 characters to analyze writing."));
    }
  } else if (request.type === "text-error") {
    switchTab("text");
    renderError(document.getElementById("text-output"), new Error(request.message));
  } else if (request.type === "video") {
    switchTab("video");
    document.getElementById("video-url-input").value = request.value || "";
    await analyzeVideoUrl(request.value || "", request.frame);
  } else if (request.type === "video-frame") {
    switchTab("video");
    await analyzeVisibleVideoFrame(request);
  } else if (request.type === "video-error") {
    switchTab("video");
    renderError(document.getElementById("video-output"), new Error(request.message));
  }
}

async function consumePending() {
  if (!hasExtensionApi || !extensionApi.storage.session) return;
  try {
    const stored = await extensionApi.storage.session.get("tayoPending");
    if (stored?.tayoPending) {
      await extensionApi.storage.session.remove("tayoPending");
      await handleExternalRequest(stored.tayoPending);
    }
  } catch (error) {
    console.warn("Could not read pending request:", error);
  }
}

/* ---------------- Settings dialog ---------------- */

function wireSettings() {
  const dialog = document.getElementById("settings-dialog");
  const button = document.getElementById("settings-button");
  const form = document.getElementById("settings-form");
  const urlInput = document.getElementById("backend-url");
  const status = document.getElementById("settings-status");

  button.addEventListener("click", () => {
    urlInput.value = state.backendUrl;
    status.textContent = "";
    dialog.showModal();
  });

  dialog.querySelectorAll("[data-close-dialog]").forEach((node) => {
    node.addEventListener("click", () => dialog.close());
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = urlInput.value.trim();
    if (!/^https?:\/\//i.test(url)) {
      status.textContent = "Enter a full http:// or https:// URL.";
      return;
    }
    await saveSettings(url);
    status.textContent = "Saved. Checking connection…";
    const health = await checkHealth(true);
    status.textContent = health
      ? `Connected to backend v${health.version || "?"} on ${health.models?.device || "cpu"}.`
      : "Saved, but the backend did not respond.";
    setTimeout(() => dialog.close(), 900);
  });

  document.getElementById("clear-data").addEventListener("click", async () => {
    if (hasExtensionApi && extensionApi.storage.local) {
      await extensionApi.storage.local.clear();
      if (extensionApi.storage.session) await extensionApi.storage.session.clear();
    }
    try {
      localStorage.removeItem("tayoBackend");
    } catch {
      /* ignore */
    }
    state.backendUrl = DEFAULT_BACKEND;
    urlInput.value = DEFAULT_BACKEND;
    status.textContent = "Extension settings cleared. Editorial database was kept.";
    checkHealth();
  });

  document.getElementById("retry-health").addEventListener("click", () => checkHealth(true));
}

/* ---------------- Init ---------------- */

async function init() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  wireFactcheck();
  wireText();
  wireImage();
  wireVideo();
  wireUrl();
  wireEditorial();
  wireSettings();

  await loadSettings();
  await checkHealth();
  await loadEditorialSources();
  await consumePending();

  if (hasExtensionApi && extensionApi.storage.onChanged) {
    extensionApi.storage.onChanged.addListener((changes, area) => {
      if (area === "session" && changes.tayoPending?.newValue) {
        const pending = changes.tayoPending.newValue;
        extensionApi.storage.session.remove("tayoPending").catch(() => {});
        handleExternalRequest(pending);
      }
    });
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
