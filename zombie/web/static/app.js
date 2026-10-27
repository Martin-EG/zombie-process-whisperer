// Web UI script. Every server value is untrusted and is rendered with textContent only (S5).
"use strict";

const KIND_LABELS = {
  git_branch: "branch",
  docker_container: "container",
  docker_volume: "volume",
  cron_job: "cron",
  process: "process",
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function kindLabel(kind) {
  return Object.prototype.hasOwnProperty.call(KIND_LABELS, kind) ? KIND_LABELS[kind] : "other";
}

function setStatus(text, isError) {
  const status = $("status");
  status.textContent = text || "";
  status.classList.toggle("error", Boolean(isError));
}

function detailText(body, response) {
  if (body && typeof body.detail === "string") return body.detail;
  if (body && body.detail !== undefined) return JSON.stringify(body.detail);
  if (typeof body === "string" && body) return body;
  return `Request failed with status ${response.status}.`;
}

async function api(method, path, payload) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (payload !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  const type = response.headers.get("Content-Type") || "";
  const body = type.includes("application/json") ? await response.json() : await response.text();
  if (response.status === 401) {
    throw new Error("Session ended. Restart `zombie ui` and open the new link it prints.");
  }
  if (!response.ok) throw new Error(detailText(body, response));
  return body;
}

// ------------------------------------------------------------ zombies

function ageText(days) {
  if (days === null || days === undefined) return "age unknown";
  return days === 1 ? "1 day old" : `${days} days old`;
}

function factsList(z) {
  const list = el("dl", "facts");
  const facts = Object.entries(z.facts || {});
  const rows = [["id", z.id]];
  if (!facts.some(([, value]) => value === z.location)) rows.push(["location", z.location]);
  rows.push(...facts);
  for (const [key, value] of rows) {
    list.appendChild(el("dt", null, key));
    list.appendChild(el("dd", null, value));
  }
  return list;
}

function card(z) {
  const article = el("article", `card kind-${kindLabel(z.kind)}`);
  const top = el("div", "card-top");
  top.appendChild(el("span", "badge", kindLabel(z.kind)));
  top.appendChild(el("span", "age", ageText(z.age_days)));
  article.appendChild(top);
  article.appendChild(el("h3", "card-title", z.title));
  article.appendChild(el("p", "eulogy", z.eulogy || ""));

  const details = el("details");
  details.appendChild(el("summary", null, "Facts"));
  details.appendChild(factsList(z));
  article.appendChild(details);

  if (z.buryable && z.command) {
    const button = el("button", "danger", "Bury");
    button.type = "button";
    button.addEventListener("click", () => openBury(z));
    article.appendChild(button);
  } else {
    article.appendChild(el("p", "muted reason", `Report only: ${z.reason_not_buryable || "not buryable"}`));
  }
  return article;
}

function renderCounts(zombies) {
  const counts = $("counts");
  counts.replaceChildren();
  const totals = {};
  for (const z of zombies) totals[kindLabel(z.kind)] = (totals[kindLabel(z.kind)] || 0) + 1;
  counts.appendChild(el("li", "count-total", `${zombies.length} total`));
  for (const label of Object.values(KIND_LABELS)) {
    if (totals[label]) counts.appendChild(el("li", null, `${totals[label]} ${label}`));
  }
}

function renderZombies(data) {
  $("root").textContent = data.root;
  $("model").textContent = data.model;
  renderCounts(data.zombies);
  const grid = $("zombies");
  grid.replaceChildren();
  if (data.zombies.length === 0) {
    grid.appendChild(el("p", "muted", "No zombies found. Your machine is suspiciously healthy."));
    return;
  }
  for (const z of data.zombies) grid.appendChild(card(z));
}

async function loadZombies() {
  renderZombies(await api("GET", "/api/zombies"));
}

// ------------------------------------------------------------ graveyard

function grave(entry) {
  const item = el("li", "grave");
  const head = el("div", "grave-head");
  head.appendChild(el("span", "badge", kindLabel(entry.kind)));
  head.appendChild(el("span", "grave-title", entry.title));
  item.appendChild(head);
  item.appendChild(el("p", "muted", entry.location));
  const when = `Buried ${entry.buried_at}` + (entry.sha ? ` at ${entry.sha.slice(0, 12)}` : "");
  item.appendChild(el("p", "muted", when));

  if (entry.resurrected_at) {
    item.appendChild(el("p", "risen", `Resurrected ${entry.resurrected_at}`));
  } else if (entry.reversible) {
    const button = el("button", null, "Resurrect");
    button.type = "button";
    button.addEventListener("click", () => resurrect(entry, button));
    item.appendChild(button);
  } else {
    item.appendChild(el("p", "muted", "Cannot be brought back."));
  }
  return item;
}

async function loadGraveyard() {
  const entries = await api("GET", "/api/graveyard");
  const list = $("graveyard");
  list.replaceChildren();
  $("graveyard-empty").hidden = entries.length > 0;
  for (const entry of entries.slice().reverse()) list.appendChild(grave(entry));
}

async function resurrect(entry, button) {
  button.disabled = true;
  try {
    const result = await api("POST", "/api/resurrect", { id: entry.zombie_id });
    setStatus(result.message);
  } catch (err) {
    setStatus(err.message, true);
  }
  await refresh();
}

// ------------------------------------------------------------ bury dialog

let current = null;

function updateConfirm() {
  const confirm = $("bury-confirm");
  confirm.disabled = !current || $("bury-name").value !== current.title;
  confirm.textContent = $("bury-dry-run").checked ? "Confirm (dry run)" : "Confirm";
}

function showOutput(text, isError) {
  const output = $("bury-output");
  output.hidden = false;
  output.textContent = text;
  output.classList.toggle("error", Boolean(isError));
}

function openBury(z) {
  current = z;
  const label = kindLabel(z.kind);
  $("bury-title").textContent = `Bury ${label} ${z.title}`;
  $("bury-command").textContent = z.command;
  $("bury-undo").textContent = z.reversible
    ? "Reversible: yes, from the Graveyard panel."
    : "Reversible: no.";
  $("bury-warning").hidden = Boolean(z.reversible);
  $("bury-name-hint").textContent = z.title;
  $("bury-dry-run").checked = true;
  $("bury-name").value = "";
  $("bury-output").hidden = true;
  $("bury-output").textContent = "";
  updateConfirm();
  $("bury-dialog").showModal();
  $("bury-name").focus();
}

async function confirmBury() {
  if (!current) return;
  const apply = !$("bury-dry-run").checked;
  const confirm = $("bury-confirm");
  confirm.disabled = true;
  try {
    const result = await api("POST", "/api/bury", {
      id: current.id,
      typed_name: $("bury-name").value,
      apply,
    });
    showOutput(`${result.message}\n${result.command}`);
    if (result.applied) {
      current = null;
      await refresh();
      return;
    }
  } catch (err) {
    showOutput(err.message, true);
  }
  updateConfirm();
}

// ------------------------------------------------------------ wiring

async function refresh() {
  try {
    await Promise.all([loadZombies(), loadGraveyard()]);
  } catch (err) {
    setStatus(err.message, true);
  }
}

async function rescan() {
  const button = $("rescan");
  button.disabled = true;
  setStatus("Rescanning. Eulogies can take a few seconds…");
  try {
    renderZombies(await api("POST", "/api/rescan"));
    await loadGraveyard();
    setStatus("Rescan done.");
  } catch (err) {
    setStatus(err.message, true);
  } finally {
    button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("rescan").addEventListener("click", rescan);
  $("bury-name").addEventListener("input", updateConfirm);
  $("bury-dry-run").addEventListener("change", updateConfirm);
  $("bury-confirm").addEventListener("click", confirmBury);
  $("bury-cancel").addEventListener("click", () => $("bury-dialog").close());
  $("bury-dialog").addEventListener("close", () => { current = null; });
  refresh();
});
