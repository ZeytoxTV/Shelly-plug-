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

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Erreur ${res.status}`);
  return data;
}

const fmt = (v, unit, digits = 1) =>
  v === null || v === undefined ? "—" : `${Number(v).toFixed(digits)} ${unit}`;

function fmtEnergy(wh) {
  if (wh === null || wh === undefined) return "—";
  return wh >= 1000 ? `${(wh / 1000).toFixed(2)} kWh` : `${wh.toFixed(0)} Wh`;
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
  if (status.partial) return;
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

setInterval(() => {
  if (!document.hidden) cards.forEach((c) => refresh(c, false));
}, REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) cards.forEach((c) => refresh(c, false));
});

load();
