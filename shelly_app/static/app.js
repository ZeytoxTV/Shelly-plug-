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

function fmtEur(eur) {
  if (eur === null || eur === undefined) return "—";
  if (eur > 0 && eur < 0.01) return "< 0,01 €";
  return eur.toLocaleString("fr-FR", { style: "currency", currency: "EUR" });
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
  if (status.via) {
    const d = card.device;
    el.querySelector(".meta").textContent = [
      d.mode === "cloud" ? "Cloud Shelly" : d.host,
      d.model,
      status.via === "local" ? "lu en direct (Wi‑Fi)" : "lu via le cloud",
    ].filter(Boolean).join(" · ");
  }
  const pw = el.querySelector(".power-w");
  pw.textContent = fmt(status.power, "W");
  if (status.stale) {
    // Le cloud ou la prise n'a pas répondu : dernière valeur connue
    const s = document.createElement("small");
    s.textContent = `il y a ${status.stale_age} s`;
    pw.append(s);
  }
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

const COST_REFRESH_MS = 60000;

async function refreshCost(card, force = false) {
  if (!force && Date.now() - (card.costAt || 0) < COST_REFRESH_MS) return;
  card.costAt = Date.now();
  try {
    const c = await api(`/api/devices/${card.device.id}/cost`);
    const el = card.el;
    el.querySelector(".cost-today").textContent = fmtEur(c.today.eur);
    const month = el.querySelector(".cost-month");
    month.textContent = fmtEur(c.month.eur);
    // Projection fin de mois au rythme actuel (à partir du 3e jour pour éviter les extrapolations absurdes)
    const now = new Date();
    const dayOfMonth = now.getDate() - 1 + (now.getHours() * 60 + now.getMinutes()) / 1440;
    const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
    if (c.month.eur != null && dayOfMonth >= 2) {
      const s = document.createElement("small");
      s.textContent = `≈ ${fmtEur((c.month.eur / dayOfMonth) * daysInMonth)} fin du mois`;
      month.append(s);
    }
  } catch {
    /* le coût n'est qu'indicatif : on garde l'affichage précédent */
  }
}

async function refresh(card, force = true) {
  if (card.busy) return;
  const interval = card.device.mode === "cloud" ? CLOUD_REFRESH_MS : REFRESH_MS;
  if (!force && Date.now() - (card.last || 0) < interval - 500) return;
  card.last = Date.now();
  refreshCost(card, force);
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
      const e = data.energy || {};
      tiles.innerHTML = tile("Coût 24 h", e.eur != null ? `${fmtEur(e.eur)} <small>${fmtEnergy(e.wh)}</small>` : "—") +
        tile("Moyenne", fmt(avg, "W", 0)) +
        tile("Pic", peak ? `${Math.round(peak.max)} W <small>${hhmm(peak.ts)}</small>` : "—");
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
      const eur = days.reduce((a, d) => a + (d.eur || 0), 0);
      tiles.innerHTML = tile("Coût total", days.length ? `${fmtEur(eur)} <small>${fmtEnergy(total)}</small>` : "—") +
        tile("Moyenne / jour", days.length ? `${fmtEur(eur / days.length)} <small>${fmtEnergy(total / days.length)}</small>` : "—") +
        tile("Jour max", best ? `${fmtEur(best.eur)} <small>${fmtEnergy(best.wh)} · ${dayLabel(best.date)}</small>` : "—");
      empty.hidden = days.length > 0;
      wrap.hidden = !days.length;
      if (days.length) Charts.daily(svg, data.daily, fmtEnergy);
      table.tHead.innerHTML = "<tr><th>Jour</th><th>Énergie</th><th>Coût</th></tr>";
      table.tBodies[0].innerHTML = days.slice().reverse()
        .map((d) => `<tr><td>${dayLabel(d.date)}</td><td>${fmtEnergy(d.wh)}</td><td>${fmtEur(d.eur)}</td></tr>`).join("");
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

/* --- Panneau tarifs ----------------------------------------------------- */
const SettingsPanel = (() => {
  const dlg = document.getElementById("settings-dialog");
  const err = dlg.querySelector("#settings-error");
  const list = dlg.querySelector(".tariffs");
  const tariffTpl = document.getElementById("tariff-tpl");
  const periodTpl = document.getElementById("period-tpl");

  function addPeriod(ul, p = {}) {
    const li = periodTpl.content.firstElementChild.cloneNode(true);
    li.querySelector(".p-name").value = p.name || "";
    li.querySelector(".p-price").value = p.price ?? "";
    li.querySelector(".p-start").value = p.start || "00:00";
    li.querySelector(".p-end").value = p.end || "06:00";
    li.querySelector(".p-remove").addEventListener("click", () => li.remove());
    ul.append(li);
  }

  function addTariff(t = {}) {
    const fs = tariffTpl.content.firstElementChild.cloneNode(true);
    fs.querySelector(".t-price").value = t.price ?? "";
    fs.querySelector(".t-from").value = t.from || "";
    const ul = fs.querySelector(".periods");
    (t.periods || []).forEach((p) => addPeriod(ul, p));
    fs.querySelector(".p-add").addEventListener("click", () => addPeriod(ul));
    fs.querySelector(".t-remove").addEventListener("click", () => { fs.remove(); retitle(); });
    list.append(fs);
    retitle();
  }

  function retitle() {
    const today = new Date().toLocaleDateString("sv");
    const all = [...list.children];
    all.forEach((fs, i) => {
      const from = fs.querySelector(".t-from").value;
      const next = all[i + 1]?.querySelector(".t-from").value;
      const active = (i === 0 || (from && from <= today)) && !(next && next <= today);
      fs.querySelector(".tariff-title").textContent =
        (i === 0 ? "Tarif" : "Nouveau prix") + (active ? " · en cours" : "");
    });
  }
  list.addEventListener("change", retitle);

  dlg.querySelector("#tariff-add").addEventListener("click", () => {
    const last = read().pop();
    const d = new Date();
    const firstOfNextMonth = new Date(d.getFullYear(), d.getMonth() + 1, 1).toLocaleDateString("sv");
    addTariff({ ...last, from: firstOfNextMonth });
  });

  function read() {
    return [...list.children].map((fs, i) => ({
      from: i === 0 ? null : fs.querySelector(".t-from").value || null,
      price: Number(fs.querySelector(".t-price").value),
      periods: [...fs.querySelectorAll(".period")].map((li) => ({
        name: li.querySelector(".p-name").value.trim() || "Période",
        price: Number(li.querySelector(".p-price").value),
        start: li.querySelector(".p-start").value,
        end: li.querySelector(".p-end").value,
      })),
    }));
  }

  dlg.querySelector("#settings-save").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    err.hidden = true;
    btn.disabled = true;
    try {
      await api("/api/settings", { method: "PUT", body: { pricing: { tariffs: read() } } });
      dlg.close();
      cards.forEach((c) => refreshCost(c, true));
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });
  dlg.querySelectorAll(".close").forEach((b) => b.addEventListener("click", () => dlg.close()));

  return {
    async open() {
      const { pricing } = await api("/api/settings");
      list.replaceChildren();
      pricing.tariffs.forEach(addTariff);
      err.hidden = true;
      dlg.showModal();
    },
  };
})();
document.getElementById("settings-btn").addEventListener("click", () =>
  SettingsPanel.open().catch((e) => alert(e.message)));

setInterval(() => {
  if (!document.hidden) cards.forEach((c) => refresh(c, false));
}, REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) cards.forEach((c) => refresh(c, false));
});

load();
