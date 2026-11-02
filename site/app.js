"use strict";

const DATA_URL = "listings.json";
const PAGE_SIZE = 36;
const REPO = "negarprh/Canadian-Tech-Internships-2027";

const state = {
  all: [],
  filtered: [],
  rendered: 0,
  ready: false,
  applied: new Set(),
};

const els = {
  form: document.getElementById("filters"),
  q: document.getElementById("q"),
  cycle: document.getElementById("cycle"),
  term: document.getElementById("term"),
  location: document.getElementById("location"),
  duration: document.getElementById("duration"),
  status: document.getElementById("status"),
  remote: document.getElementById("remote"),
  count: document.getElementById("count"),
  chips: document.getElementById("chips"),
  list: document.getElementById("listings"),
  skeleton: document.getElementById("skeleton"),
  empty: document.getElementById("empty"),
  loadMore: document.getElementById("load-more"),
  sentinel: document.getElementById("sentinel"),
};

const TERM_LABELS = { winter: "Winter", summer: "Summer", fall: "Fall" };

const CHECK_STORAGE_KEY = "cti:checked";
const APPLY_CLASS = "inline-flex shrink-0 items-center gap-1 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition hover:bg-primary-container focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2";
const CHECKED_CLASS = "inline-flex shrink-0 items-center gap-1 rounded-full border border-secondary/40 bg-surface-container-low px-4 py-2 text-sm font-semibold text-secondary transition hover:bg-secondary-container hover:text-on-secondary-container focus:outline-none focus-visible:ring-2 focus-visible:ring-secondary focus-visible:ring-offset-2";
const CHECKED_TAG = `<span data-checked class="inline-flex items-center gap-1 rounded-full border border-secondary/40 bg-surface px-2.5 py-1 text-xs font-medium text-secondary"><span class="material-symbols-outlined text-[14px]" aria-hidden="true">check_circle</span>Checked</span>`;

function listingKey(item) {
  return [item.company, item.role, item.location, item.posted].join("\u0001");
}

function loadChecked() {
  try {
    const raw = localStorage.getItem(CHECK_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) state.applied = new Set(parsed);
    }
  } catch (e) {
    state.applied = new Set();
  }
}

function saveChecked() {
  try {
    localStorage.setItem(CHECK_STORAGE_KEY, JSON.stringify([...state.applied]));
  } catch (e) {}
}

function isChecked(key) {
  return state.applied.has(key);
}

function markChecked(key) {
  if (!key || state.applied.has(key)) return;
  state.applied.add(key);
  saveChecked();
  refreshCardApplied(key);
  updateChips();
}

function clearChecked() {
  state.applied.clear();
  saveChecked();
}

function refreshCardApplied(key) {
  const article = [...els.list.children].find((el) => el.dataset.key === key);
  if (!article) return;
  const link = article.querySelector("a[data-apply]");
  if (link) {
    link.className = CHECKED_CLASS;
    link.title = "You already checked this posting — open the application again";
    link.setAttribute("aria-label", "Checked — open application again");
    link.innerHTML = `Checked<span class="material-symbols-outlined text-[16px]" aria-hidden="true">check_circle</span>`;
  }
  const tags = article.querySelector("[data-tags]");
  if (tags && !tags.querySelector("[data-checked]")) {
    tags.insertAdjacentHTML("beforeend", CHECKED_TAG);
  }
}

const ICON_RULES = [
  [/shopify|amazon|walmart|home depot|etsy|faire|wayfair|costco|loblaws|canadian tire|indigo/i, "shopping_bag", "text-primary"],
  [/royal bank|rbc|bmo|bank of montreal|scotiabank|tangerine|wealthsimple|manulife|sun life|definity|intact|desjardins|national bank|td bank|interac|stripe|paypal|visa|mastercard|koho|questrade/i, "account_balance", "text-secondary"],
  [/1password|okta|fortinet|palo alto|cyber|security|entrust|beyondtrust|sailpoint|proofpoint|tenable|duo/i, "lock", "text-primary"],
  [/cohere|mila|deepmind|openai|hugging|anthropic|\bai\b|machine learning|kinaxis|vector institute/i, "psychology", "text-primary"],
  [/intel|amd|nvidia|marvell|altera|qualcomm|micron|synopsys|cadence|arm|broadcom|onsemi|microchip|analog devices|texas instruments|semiconductor/i, "memory", "text-secondary"],
  [/microsoft|google|oracle|salesforce|sap|snowflake|databricks|servicenow|workday|splunk|elastic|datadog|confluent|hashicorp|red hat|vmware|cloud|docusign|atlassian|slack/i, "cloud", "text-secondary"],
  [/telus|rogers|bell canada|videotron|nokia|ericsson|huawei|zte|ciena|juniper|arista|motorola|sierra wireless/i, "cell_tower", "text-primary"],
  [/electronic arts|\bea\b|ubisoft|rockstar|bioware|unity|epic games|gameloft|activision|take-two|roblox/i, "sports_esports", "text-primary"],
  [/bombardier|mda|mda space|magellan|pratt|aerospace|\bcae\b|air canada|westjet|hopper|porter/i, "flight", "text-secondary"],
  [/accenture|deloitte|kpmg|pwc|\bey\b|mckinsey|bain|boston consulting|ibm|capgemini|\bcgi\b|cognizant|infosys|tata|slalom|thoughtworks|consulting/i, "business_center", "text-secondary"],
  [/\bgm\b|general motors|ford|magna|stellantis|tesla|rivian|bosch|aptiv|continental|automotive/i, "directions_car", "text-primary"],
  [/health|pharma|medical|hospital|sickkids|sunnybrook|medtronic|abbott|genentech|amgen|pfizer|novartis|astrazeneca|moderna|bayer|pointclickcare|telus health/i, "health_and_safety", "text-primary"],
  [/university|college|institute|waterloo|toronto|ubc|mcgill|mcmaster|queen|alberta|ottawa|\bsfu\b|york|concordia|polytechnique|research/i, "school", "text-secondary"],
  [/autodesk|adobe|dassault|ansys|\bptc\b|siemens|manufacturing/i, "precision_manufacturing", "text-secondary"],
  [/hydro|energy|enbridge|suncor|\btc energy\b|\bpower\b|electric|\bopg\b|atco|transalta|kinectrics/i, "bolt", "text-primary"],
  [/defence|defense|general dynamics|lockheed|raytheon|thales|leonardo|calian/i, "shield", "text-secondary"],
  [/insurance|aviva|co-operators|economical|allstate|belairdirect/i, "verified_user", "text-secondary"],
];

function iconFor(company) {
  for (const [re, icon, color] of ICON_RULES) {
    if (re.test(company)) return { icon, color };
  }
  return { icon: "apartment", color: "text-on-surface-variant" };
}

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function termSeason(workTerm) {
  return workTerm ? workTerm.split(" ")[0].toLowerCase() : "";
}

function formatPosted(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "";
  return `Posted ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })}`;
}

function inDurationBucket(months, bucket) {
  if (bucket === "unknown") return months == null;
  if (months == null) return false;
  const [min, max] = bucket.split("-").map(Number);
  return months >= min && months <= max;
}

function matches(item) {
  const q = els.q.value.trim().toLowerCase();
  if (q) {
    const hay = `${item.company} ${item.role} ${item.location} ${item.work_term}`.toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (els.cycle.value && item.cycle !== els.cycle.value) return false;
  if (els.term.value && termSeason(item.work_term) !== els.term.value) return false;
  if (els.status.value !== "all" && item.status !== els.status.value) return false;
  if (els.remote.checked && !item.remote) return false;
  if (els.location.value) {
    const [kind, value] = els.location.value.split(":", 2);
    if (kind === "remote" && !item.remote) return false;
    if (kind === "province" && !(item.provinces || []).includes(value)) return false;
  }
  if (els.duration.value && !inDurationBucket(item.duration_months, els.duration.value)) return false;
  return true;
}

function tag(text, classes) {
  return `<span class="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${classes}">${esc(text)}</span>`;
}

function cardHTML(item) {
  const { icon, color } = iconFor(item.company || "");
  const key = listingKey(item);
  const checked = state.applied.has(key);
  const tags = [];
  if (item.work_term) tags.push(tag(item.work_term, "bg-primary-fixed text-on-primary-fixed"));
  tags.push(tag(item.location || "Location not listed", "bg-surface-container text-on-surface-variant"));
  if (item.duration_months != null) tags.push(tag(`${item.duration_months} mo`, "bg-surface-container text-on-surface-variant"));
  if (item.remote) tags.push(tag("Remote", "bg-secondary-container text-on-secondary-container"));
  if (item.status === "closed") tags.push(tag("Closed", "bg-surface-container-high text-on-surface-variant"));
  if (checked) tags.push(CHECKED_TAG);

  const posted = formatPosted(item.posted);
  let apply;
  if (!item.url) {
    apply = `<span class="inline-flex shrink-0 items-center rounded-full bg-surface-container px-4 py-2 text-sm font-semibold text-on-surface-variant">Closed</span>`;
  } else if (checked) {
    apply = `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" data-apply data-key="${esc(key)}"
          class="${CHECKED_CLASS}" title="You've already checked this posting — open the application again" aria-label="Checked — open application again">
         Checked<span class="material-symbols-outlined text-[16px]" aria-hidden="true">check_circle</span>
       </a>`;
  } else {
    apply = `<a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" data-apply data-key="${esc(key)}"
          class="${APPLY_CLASS}">
         Apply<span class="material-symbols-outlined text-[16px]" aria-hidden="true">arrow_outward</span>
       </a>`;
  }

  return `<article data-key="${esc(key)}" class="flex flex-col gap-3.5 rounded-2xl border border-outline-variant bg-surface p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md sm:p-5">
    <div class="flex items-start gap-3">
      <span class="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-surface-container ${color}" aria-hidden="true">
        <span class="material-symbols-outlined text-[22px]">${icon}</span>
      </span>
      <div class="min-w-0">
        <h2 class="text-[15px] font-bold leading-snug text-on-surface">${esc(item.role)}</h2>
        <p class="mt-0.5 flex items-center gap-1 text-sm text-on-surface-variant">
          <span class="truncate">${esc(item.company)}</span>
          <span class="material-symbols-outlined text-[15px] text-secondary" title="Reviewed listing" aria-label="Reviewed listing">verified</span>
        </p>
      </div>
    </div>
    <div data-tags class="flex flex-wrap gap-1.5">${tags.join("")}</div>
    <div class="mt-auto flex items-center justify-between gap-3 pt-1">
      <span class="text-xs text-on-surface-variant">${esc(posted)}</span>
      ${apply}
    </div>
  </article>`;
}

function renderBatch() {
  const next = state.filtered.slice(state.rendered, state.rendered + PAGE_SIZE);
  if (next.length === 0) return false;
  els.list.insertAdjacentHTML("beforeend", next.map(cardHTML).join(""));
  state.rendered += next.length;
  return true;
}

function updateCount() {
  if (!state.ready) return;
  els.count.textContent = `Showing ${state.filtered.length} of ${state.all.length} internships`;
}

function chip(label, onClear) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "inline-flex items-center gap-1 rounded-full bg-primary-fixed px-3 py-1 text-xs font-semibold text-on-primary-fixed transition hover:opacity-80";
  b.innerHTML = `<span>${esc(label)}</span><span class="material-symbols-outlined text-[15px]" aria-hidden="true">close</span>`;
  b.addEventListener("click", () => { onClear(); applyFilters(); });
  return b;
}

function updateChips() {
  els.chips.replaceChildren();
  const q = els.q.value.trim();
  if (q) els.chips.append(chip(`“${q}”`, () => { els.q.value = ""; }));
  if (els.cycle.value) els.chips.append(chip(`${els.cycle.value} cycle`, () => { els.cycle.value = ""; }));
  if (els.term.value) els.chips.append(chip(TERM_LABELS[els.term.value] || els.term.value, () => { els.term.value = ""; }));
  if (els.location.value) els.chips.append(chip(els.location.selectedOptions[0] ? els.location.selectedOptions[0].textContent : "Location", () => { els.location.value = ""; }));
  if (els.duration.value) els.chips.append(chip(els.duration.options[els.duration.selectedIndex].text, () => { els.duration.value = ""; }));
  if (els.status.value !== "open") els.chips.append(chip(els.status.options[els.status.selectedIndex].text, () => { els.status.value = "all"; }));
  if (els.remote.checked) els.chips.append(chip("Remote only", () => { els.remote.checked = false; }));
  if (state.applied.size) els.chips.append(chip(`Clear checked (${state.applied.size})`, () => clearChecked()));
}

function updateSentinel() {
  els.sentinel.style.display = state.rendered < state.filtered.length ? "" : "none";
}

function setEmpty(visible) {
  els.empty.classList.toggle("hidden", !visible);
  els.empty.classList.toggle("flex", visible);
}

function setSkeleton(visible) {
  els.skeleton.classList.toggle("hidden", !visible);
}

function applyFilters() {
  state.filtered = state.all.filter(matches);
  state.rendered = 0;
  els.list.replaceChildren();
  renderBatch();
  setEmpty(state.filtered.length === 0);
  updateCount();
  updateChips();
  updateSentinel();
}

let loadingMore = false;
function loadMore() {
  if (loadingMore || state.rendered >= state.filtered.length) return;
  loadingMore = true;
  els.loadMore.classList.remove("hidden");
  els.loadMore.classList.add("flex");
  requestAnimationFrame(() => {
    renderBatch();
    updateCount();
    els.loadMore.classList.add("hidden");
    els.loadMore.classList.remove("flex");
    loadingMore = false;
  });
}

function populateLocations() {
  const provinces = new Set();
  let hasRemote = false;
  for (const item of state.all) {
    if (item.remote) hasRemote = true;
    for (const p of item.provinces || []) provinces.add(p);
  }
  const frag = document.createDocumentFragment();
  if (hasRemote) {
    const o = document.createElement("option");
    o.value = "remote";
    o.textContent = "Remote";
    frag.append(o);
  }
  for (const p of [...provinces].sort()) {
    const o = document.createElement("option");
    o.value = `province:${p}`;
    o.textContent = p;
    frag.append(o);
  }
  els.location.append(frag);
}

function initApplyTracking() {
  els.list.addEventListener("click", (e) => {
    const link = e.target.closest("a[data-apply]");
    if (!link) return;
    const key = link.dataset.key;
    if (key) markChecked(key);
  });
}

function initFilters() {
  let debounce;
  els.form.addEventListener("input", (e) => {
    if (e.target === els.q) {
      clearTimeout(debounce);
      debounce = setTimeout(applyFilters, 120);
    } else {
      applyFilters();
    }
  });
  els.form.addEventListener("reset", () => setTimeout(applyFilters, 0));
  document.querySelectorAll("[data-reset-filters]").forEach((b) =>
    b.addEventListener("click", () => {
      els.form.reset();
      setTimeout(applyFilters, 0);
    }));
  document.querySelectorAll(".cohort-chip").forEach((b) =>
    b.addEventListener("click", () => {
      els.term.value = b.dataset.season || "";
      els.cycle.value = b.dataset.cycle || "";
      applyFilters();
      els.list.scrollIntoView({ behavior: "smooth", block: "start" });
    }));
}

function initInfiniteScroll() {
  if (!("IntersectionObserver" in window)) return;
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) loadMore();
  }, { rootMargin: "600px 0px" });
  io.observe(els.sentinel);
}

function initAddForm() {
  const modal = document.getElementById("add-modal");
  const form = document.getElementById("add-form");
  const confirm = document.getElementById("add-confirm");
  const error = document.getElementById("add-error");
  if (!modal || !form) return;

  const open = () => {
    modal.classList.remove("hidden");
    document.body.classList.add("overflow-hidden");
    const first = form.querySelector("input");
    if (first) first.focus();
  };
  const close = () => {
    modal.classList.add("hidden");
    document.body.classList.remove("overflow-hidden");
    hideError();
  };

  document.querySelectorAll("[data-open-add]").forEach((b) => b.addEventListener("click", open));
  document.querySelectorAll("[data-close-add]").forEach((b) => b.addEventListener("click", close));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) close();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const company = document.getElementById("add-company").value.trim();
    const role = document.getElementById("add-role").value.trim();
    const location = document.getElementById("add-location").value.trim();
    const link = document.getElementById("add-link").value.trim();
    const date = document.getElementById("add-date").value.trim();

    if (!company || !role || !location || !link || !date) return showError("Please fill in every field.");
    if (!/^https?:\/\/.+/i.test(link)) return showError("The application link must start with http:// or https://");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return showError("Please choose a valid date (YYYY-MM-DD).");
    if (new Date(`${date}T00:00:00Z`) > new Date()) return showError("The posted date can't be in the future.");
    if (!confirm.checked) return showError("Please confirm you verified the link and that the role is open.");

    const params = new URLSearchParams({
      template: "add-internship.yml",
      title: `Add internship: ${company} - ${role}`,
      company,
      role,
      location,
      link,
      date,
    });
    window.open(`https://github.com/${REPO}/issues/new?${params.toString()}`, "_blank", "noopener,noreferrer");
    close();
    form.reset();
  });

  function showError(msg) {
    error.textContent = msg;
    error.classList.remove("hidden");
  }
  function hideError() {
    error.classList.add("hidden");
  }
}

function initThemeToggle() {
  const btn = document.querySelector("[data-theme-toggle]");
  if (!btn) return;
  const sync = () => btn.setAttribute("aria-pressed", String(document.documentElement.classList.contains("dark")));
  sync();
  btn.addEventListener("click", () => {
    const dark = document.documentElement.classList.toggle("dark");
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch (e) {}
    sync();
  });
}

async function init() {
  setSkeleton(true);
  loadChecked();
  try {
    const res = await fetch(DATA_URL, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.all = Array.isArray(data) ? data : (data.listings || []);
    state.ready = true;
    populateLocations();
    applyFilters();
  } catch (err) {
    els.list.innerHTML = "";
    setEmpty(true);
    const title = els.empty.querySelector("p.font-semibold");
    const detail = els.empty.querySelector("p.text-sm");
    if (title) title.textContent = "Couldn't load listings";
    if (detail) detail.textContent = "The data file failed to load. Try refreshing the page.";
    els.count.textContent = "No data available";
    console.error(err);
  } finally {
    setSkeleton(false);
  }
}

initFilters();
initApplyTracking();
initInfiniteScroll();
initAddForm();
initThemeToggle();
init();