"use strict";

const state = { listings: [] };

const els = {
  q: document.getElementById("q"),
  cycle: document.getElementById("cycle"),
  term: document.getElementById("term"),
  location: document.getElementById("location"),
  duration: document.getElementById("duration"),
  status: document.getElementById("status"),
  remote: document.getElementById("remote"),
  reset: document.getElementById("reset"),
  count: document.getElementById("count"),
  generated: document.getElementById("generated"),
  list: document.getElementById("listings"),
  empty: document.getElementById("empty"),
};

function inDurationBucket(months, bucket) {
  if (bucket === "unknown") return months == null;
  if (months == null) return false;
  const [min, max] = bucket.split("-").map(Number);
  return months >= min && months <= max;
}

function termSeason(workTerm) {
  return workTerm ? workTerm.split(" ")[0].toLowerCase() : "";
}

function matches(item) {
  const q = els.q.value.trim().toLowerCase();
  if (q && !`${item.company} ${item.role}`.toLowerCase().includes(q)) return false;
  if (els.cycle.value && item.cycle !== els.cycle.value) return false;
  if (els.term.value && termSeason(item.work_term) !== els.term.value) return false;
  if (els.status.value !== "all" && item.status !== els.status.value) return false;
  if (els.remote.checked && !item.remote) return false;
  if (els.location.value) {
    const [kind, value] = els.location.value.split(":", 2);
    if (kind === "remote" && !item.remote) return false;
    if (kind === "province" && !item.provinces.includes(value)) return false;
  }
  if (els.duration.value && !inDurationBucket(item.duration_months, els.duration.value)) return false;
  return true;
}

function makeTag(text, variant) {
  const span = document.createElement("span");
  span.className = "tag" + (variant ? ` ${variant}` : "");
  span.textContent = text;
  return span;
}

function renderCard(item) {
  const li = document.createElement("li");
  li.className = "card";

  const info = document.createElement("div");
  const role = document.createElement("p");
  role.className = "role";
  role.textContent = item.role;
  const company = document.createElement("p");
  company.className = "company";
  company.textContent = item.company;
  info.append(role, company);

  const tags = document.createElement("div");
  tags.className = "tags";
  tags.append(makeTag(item.location || "Location not listed", "location"));
  if (item.work_term) tags.append(makeTag(item.work_term, "term"));
  if (item.duration_months) tags.append(makeTag(`${item.duration_months} months`, "duration"));
  if (item.remote) tags.append(makeTag("Remote", "remote"));
  if (item.posted) tags.append(makeTag(`Posted ${item.posted}`, "posted"));
  tags.append(makeTag(item.status === "open" ? "Open" : "Closed", `status-${item.status}`));
  info.append(tags);

  const apply = document.createElement(item.url ? "a" : "span");
  apply.className = "apply" + (item.url ? "" : " closed");
  apply.textContent = item.url ? "Apply" : "Closed";
  if (item.url) {
    apply.href = item.url;
    apply.target = "_blank";
    apply.rel = "noopener noreferrer";
  }

  li.append(info, apply);
  return li;
}

function render() {
  const visible = state.listings.filter(matches);
  els.list.replaceChildren(...visible.map(renderCard));
  els.empty.hidden = visible.length !== 0;
  els.count.textContent = `Showing ${visible.length} of ${state.listings.length} internships`;
}

function populateLocations() {
  const provinces = new Set();
  state.listings.forEach((item) => item.provinces.forEach((p) => provinces.add(p.toUpperCase())));
  const options = [{ value: "remote:", label: "Remote" }]
    .concat([...provinces].sort().map((p) => ({ value: `province:${p}`, label: p })));
  for (const { value, label } of options) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    els.location.append(option);
  }
}

async function init() {
  try {
    const response = await fetch("listings.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.listings = Array.isArray(data.listings) ? data.listings : [];
    populateLocations();
    els.generated.textContent = `${data.count} internships from the repository listings · data generated automatically`;
    render();
  } catch (error) {
    els.count.textContent = "Could not load listings data.";
    els.empty.hidden = false;
    els.empty.textContent = "Could not load listings.json. If previewing locally, serve the folder over HTTP (e.g. `python3 -m http.server`).";
    console.error(error);
  }
}

document.getElementById("filters").addEventListener("input", render);
els.reset.addEventListener("click", () => setTimeout(render, 0));

init();