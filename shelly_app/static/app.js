"use strict";

const REFRESH_MS = 3000;
const CLOUD_REFRESH_MS = 10000;
const list = document.getElementById("devices");
const empty = document.getElementById("empty");
const tpl = document.getElementById("card-tpl");
const dialog = document.getElementById("add-dialog");
const form = document.getElementById("add-form");
const addError = document.getElementById("add-error");
const cards = new Map();

// Après une mise à jour du serveur, recharge la page (sauf si un panneau est ouvert).
let appVersion = null;
function checkVersion(v) {
  if (!v) return;
  if (appVersion === null) appVersion = v;
  else if (v !== appVersion && !document.querySelector("dialog[open]")) location.reload();
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  checkVersion(res.headers.get("X-App-Version"));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Erreur ${res.status}`);
  return data;
}

const num = (v, digits) =>
  Number(v).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const fmt = (v, unit, digits = 1) =>
  v === null || v === undefined ? "—" : `${num(v, digits)} ${unit}`;

function fmtEnergy(wh) {
  if (wh === null || wh === undefined) return "—";
  return wh >= 1000 ? `${num(wh / 1000, 2)} kWh` : `${num(wh, 0)} Wh`;
}

function fmtDuration(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function render(card, status) {
  const el = card.el;
  card.on = status.on;
  el.classList.remove("offline");
  el.querySelector(".error").hidden = true;
  const btn = el.querySelector(".power");
  btn.setAttribute("aria-pressed", String(status.on));
  btn.querySelector(".state").textContent = status.on ? "Allumée" : "Éteinte";
  const pending = el.querySelector(".pending");
  const pendingText = Schedule.pendingText(status.automation);
  pending.textContent = pendingText;
  pending.hidden = !pendingText;
  if (status.partial) return;
  card.status = status;
  el.querySelector(".power-w").textContent = fmt(status.power, "W");
  el.querySelector(".energy").textContent = fmtEnergy(status.energy_wh);
  el.querySelector(".voltage").textContent = fmt(status.voltage, "V", 0);
  el.querySelector(".temp").textContent = fmt(status.temperature, "°C");
  const t = status.timer_remaining;
  el.querySelector(".timer-info").textContent =
    t ? `${status.on ? "Extinction" : "Allumage"} dans ${fmtDuration(t)}` : "";
}

function showError(card, message) {
  const el = card.el;
  card.on = undefined;
  el.classList.add("offline");
  const err = el.querySelector(".error");
  err.textContent = message;
  err.hidden = false;
  el.querySelector(".power .state").textContent = "Hors ligne";
  el.querySelector(".power").setAttribute("aria-pressed", "false");
}

async function refresh(card, force = true) {
  if (card.busy) return;
  const interval = card.device.mode === "cloud" ? CLOUD_REFRESH_MS : REFRESH_MS;
  if (!force && Date.now() - (card.last || 0) < interval - 500) return;
  card.last = Date.now();
  try {
    render(card, await api(`/api/devices/${card.device.id}/status`));
  } catch (e) {
    showError(card, e.message);
  }
}

function createCard(device) {
  const el = tpl.content.firstElementChild.cloneNode(true);
  const card = { device, el, busy: false };
  el.querySelector(".name").textContent = device.name;
  el.querySelector(".meta").textContent =
    [device.host, device.model].filter(Boolean).join(" · ");

  el.querySelector(".power").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    const timer = Number(el.querySelector(".timer-select").value) || undefined;
    card.busy = true;
    btn.disabled = true;
    try {
      render(card, await api(`/api/devices/${device.id}/switch`, {
        method: "POST",
        body: { action: card.on === undefined ? "toggle" : card.on ? "off" : "on", timer },
      }));
      el.querySelector(".timer-select").value = "0";
    } catch (e) {
      showError(card, e.message);
    } finally {
      card.busy = false;
      btn.disabled = false;
    }
  });

  el.querySelector(".open-chart").addEventListener("click", () => ChartPanel.open(card));
  el.querySelector(".open-schedule").addEventListener("click", () =>
    Schedule.open(card).catch((e) => alert(e.message)));

  el.querySelector(".remove").addEventListener("click", async () => {
    if (!confirm(`Supprimer « ${device.name} » ?`)) return;
    try {
      await api(`/api/devices/${device.id}`, { method: "DELETE" });
      el.remove();
      cards.delete(device.id);
      updateEmpty();
    } catch (e) {
      alert(e.message);
    }
  });

  cards.set(device.id, card);
  list.append(el);
  refresh(card);
}

function updateEmpty() {
  empty.hidden = cards.size > 0;
}

async function load() {
  try {
    const devices = await api("/api/devices");
    devices.forEach(createCard);
  } catch (e) {
    empty.textContent = `Impossible de joindre le serveur : ${e.message}`;
  }
  updateEmpty();
}

document.getElementById("add-btn").addEventListener("click", () => {
  form.reset();
  applyMode();
  addError.hidden = true;
  dialog.showModal();
});
function applyMode() {
  const mode = form.elements.mode.value;
  form.querySelectorAll("fieldset[data-mode]").forEach((fs) => {
    const active = fs.dataset.mode === mode;
    fs.hidden = !active;
    fs.disabled = !active;
  });
}
form.querySelectorAll("input[name=mode]").forEach((r) => r.addEventListener("change", applyMode));

document.getElementById("add-cancel").addEventListener("click", () => dialog.close());

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = document.getElementById("add-submit");
  submit.disabled = true;
  submit.textContent = "Connexion…";
  addError.hidden = true;
  try {
    const device = await api("/api/devices", {
      method: "POST",
      body: Object.fromEntries(new FormData(form)),
    });
    createCard(device);
    updateEmpty();
    dialog.close();
  } catch (e) {
    addError.textContent = e.message;
    addError.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Ajouter";
  }
});

/* --- Panneau graphique --------------------------------------------------- */
const ChartPanel = (() => {
  const dlg = document.getElementById("chart-dialog");
  const svg = dlg.querySelector(".chart");
  const tiles = dlg.querySelector(".tiles");
  const empty = dlg.querySelector(".chart-empty");
  const table = dlg.querySelector(".table-view table");
  let card = null, data = null;

  const tile = (k, v) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });

  function draw() {
    if (!data) return;
    const wrap = dlg.querySelector(".chart-wrap");
    wrap.querySelector(".tooltip").hidden = true;
    if (data.power) {
      const pts = data.power.filter((p) => p.avg != null);
      const avg = pts.length ? pts.reduce((a, p) => a + p.avg, 0) / pts.length : null;
      const peak = pts.length ? pts.reduce((a, p) => (p.max > a.max ? p : a)) : null;
      const wh = pts.reduce((a, p) => a + p.avg / 6, 0);
      tiles.innerHTML = tile("Moyenne", fmt(avg, "W", 0)) +
        tile("Pic", peak ? `${Math.round(peak.max)} W <small>${hhmm(peak.ts)}</small>` : "—") +
        tile("Énergie 24 h", pts.length ? fmtEnergy(wh) : "—");
      empty.hidden = pts.length > 0;
      wrap.hidden = !pts.length;
      if (pts.length) Charts.power(svg, data.power);
      table.tHead.innerHTML = "<tr><th>Heure</th><th>Moyenne</th><th>Pic</th></tr>";
      table.tBodies[0].innerHTML = pts.slice().reverse()
        .map((p) => `<tr><td>${hhmm(p.ts)}</td><td>${Math.round(p.avg)} W</td><td>${Math.round(p.max)} W</td></tr>`).join("");
    } else {
      const days = data.daily.filter((d) => d.wh != null);
      const total = days.reduce((a, d) => a + d.wh, 0);
      const best = days.length ? days.reduce((a, d) => (d.wh > a.wh ? d : a)) : null;
      const dayLabel = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short" });
      tiles.innerHTML = tile("Total", days.length ? fmtEnergy(total) : "—") +
        tile("Moyenne / jour", days.length ? fmtEnergy(total / days.length) : "—") +
        tile("Jour max", best ? `${fmtEnergy(best.wh)} <small>${dayLabel(best.date)}</small>` : "—");
      empty.hidden = days.length > 0;
      wrap.hidden = !days.length;
      if (days.length) Charts.daily(svg, data.daily, fmtEnergy);
      table.tHead.innerHTML = "<tr><th>Jour</th><th>Énergie</th></tr>";
      table.tBodies[0].innerHTML = days.slice().reverse()
        .map((d) => `<tr><td>${dayLabel(d.date)}</td><td>${fmtEnergy(d.wh)}</td></tr>`).join("");
    }
  }

  async function load() {
    const range = dlg.querySelector("input[name=range]:checked").value;
    try {
      data = await api(`/api/devices/${card.device.id}/history?range=${range}`);
      draw();
    } catch (e) {
      tiles.innerHTML = "";
      empty.textContent = e.message;
      empty.hidden = false;
    }
  }

  dlg.querySelectorAll("input[name=range]").forEach((r) => r.addEventListener("change", load));
  dlg.querySelector(".close").addEventListener("click", () => dlg.close());
  window.addEventListener("resize", () => dlg.open && draw());

  return {
    open(c) {
      card = c;
      data = null;
      dlg.querySelector(".dev-name").textContent = c.device.name;
      dlg.showModal();
      load();
    },
  };
})();

setInterval(() => {
  if (!document.hidden) cards.forEach((c) => refresh(c, false));
}, REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) cards.forEach((c) => refresh(c, false));
});

load();
