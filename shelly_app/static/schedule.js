"use strict";
/* Éditeur de programmation hebdomadaire + protection anti-coupure. */

const Schedule = (() => {
  const DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
  const dlg = document.getElementById("schedule-dialog");
  const week = dlg.querySelector(".week");
  const daysBox = dlg.querySelector(".days");
  const err = dlg.querySelector("#sched-error");
  const pendingEl = dlg.querySelector(".pending");
  let state = null; // { device, card, schedule }
  let selectedDays = new Set();

  DAYS.forEach((name, i) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = name[0];
    b.title = name;
    b.setAttribute("aria-label", name);
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      selectedDays.has(i) ? selectedDays.delete(i) : selectedDays.add(i);
      renderDayPicker();
    });
    daysBox.append(b);
  });

  function renderDayPicker() {
    [...daysBox.children].forEach((b, i) => b.setAttribute("aria-pressed", String(selectedDays.has(i))));
  }

  dlg.querySelectorAll(".quick .chip").forEach((b) =>
    b.addEventListener("click", () => {
      selectedDays = new Set(b.dataset.days.split(",").map(Number));
      renderDayPicker();
    }));

  function renderWeek() {
    week.replaceChildren();
    const rules = state.schedule.rules;
    DAYS.forEach((name, day) => {
      const li = document.createElement("li");
      li.innerHTML = `<span class="day" title="${name}">${name.slice(0, 3)}.</span><div class="slots"></div>`;
      const slots = li.querySelector(".slots");
      const todays = rules.filter((r) => r.days.includes(day)).sort((a, b) => a.time.localeCompare(b.time));
      if (!todays.length) slots.innerHTML = `<span class="none">Rien de prévu</span>`;
      todays.forEach((r) => {
        const s = document.createElement("span");
        s.className = `slot ${r.action}`;
        s.innerHTML = `<span class="ic"></span><b>${r.time}</b>`;
        s.title = `${r.action === "on" ? "Allumer" : "Éteindre"} à ${r.time}`;
        const del = document.createElement("button");
        del.type = "button";
        del.textContent = "×";
        del.setAttribute("aria-label", `Retirer « ${r.action === "on" ? "allumer" : "éteindre"} à ${r.time} » le ${name.toLowerCase()}`);
        del.addEventListener("click", () => {
          r.days = r.days.filter((d) => d !== day);
          state.schedule.rules = rules.filter((x) => x.days.length);
          renderWeek();
        });
        s.append(del);
        slots.append(s);
      });
      week.append(li);
    });
  }

  function renderProtect() {
    const p = state.schedule.protect;
    const enabled = dlg.querySelector("#protect-enabled");
    enabled.checked = p.enabled;
    dlg.querySelector("#protect-threshold").value = p.threshold_w;
    dlg.querySelector("#protect-idle").value = p.idle_minutes;
    syncProtect();
    const now = state.card.status?.power;
    dlg.querySelector("#protect-hint").textContent =
      "À l'heure d'un arrêt programmé, si l'appareil consomme plus que le seuil, la prise reste allumée " +
      "et ne s'éteint qu'après être resté sous le seuil pendant la durée choisie. " +
      "Choisis un seuil entre la veille du PC éteint et sa conso quand il est allumé." +
      (now != null ? ` Conso actuelle : ${Math.round(now)} W.` : "");
  }

  function syncProtect() {
    const on = dlg.querySelector("#protect-enabled").checked;
    const fields = dlg.querySelector(".protect-fields");
    fields.setAttribute("aria-disabled", String(!on));
    fields.querySelectorAll("input").forEach((i) => (i.disabled = !on));
  }
  dlg.querySelector("#protect-enabled").addEventListener("change", syncProtect);

  function renderPending(pending) {
    const text = pendingText(pending);
    pendingEl.textContent = text || "";
    pendingEl.hidden = !text;
  }

  function pendingText(pending) {
    if (!pending) return "";
    return `⏸ Arrêt de ${pending.rule_time} reporté : l'appareil est encore utilisé. ` +
      (pending.low_since ? "Consommation basse détectée, extinction imminente." : "La prise s'éteindra quand il sera au repos.");
  }

  function renderEvents(events) {
    const ul = dlg.querySelector(".events");
    ul.replaceChildren();
    if (!events.length) {
      ul.innerHTML = "<li>Aucune action automatique pour l'instant.</li>";
      return;
    }
    for (const e of events) {
      const li = document.createElement("li");
      const d = new Date(e.ts * 1000);
      const t = document.createElement("time");
      t.textContent = d.toLocaleString("fr-FR", { weekday: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
      li.append(t, e.message);
      ul.append(li);
    }
  }

  dlg.querySelector("#rule-add").addEventListener("click", () => {
    const time = dlg.querySelector("#rule-time").value;
    const action = dlg.querySelector("#rule-action").value;
    err.hidden = true;
    if (!time) return showErr("Choisis une heure.");
    if (!selectedDays.size) return showErr("Choisis au moins un jour.");
    const days = [...selectedDays].sort();
    // Même heure + même action : on fusionne les jours
    const same = state.schedule.rules.find((r) => r.time === time && r.action === action);
    if (same) same.days = [...new Set([...same.days, ...days])].sort();
    else state.schedule.rules.push({ id: Math.random().toString(36).slice(2, 10), time, action, days });
    // Un jour ne peut pas avoir deux actions différentes à la même heure
    for (const r of state.schedule.rules) {
      if (r.time === time && r.action !== action) r.days = r.days.filter((d) => !selectedDays.has(d));
    }
    state.schedule.rules = state.schedule.rules.filter((r) => r.days.length);
    renderWeek();
  });

  function showErr(m) {
    err.textContent = m;
    err.hidden = false;
  }

  dlg.querySelector("#sched-save").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget;
    err.hidden = true;
    const s = state.schedule;
    s.enabled = dlg.querySelector("#sched-enabled").checked;
    s.protect = {
      enabled: dlg.querySelector("#protect-enabled").checked,
      threshold_w: Number(dlg.querySelector("#protect-threshold").value),
      idle_minutes: Number(dlg.querySelector("#protect-idle").value),
    };
    btn.disabled = true;
    try {
      await api(`/api/devices/${state.device.id}/schedule`, { method: "PUT", body: s });
      dlg.close();
    } catch (e) {
      showErr(e.message);
    } finally {
      btn.disabled = false;
    }
  });

  dlg.querySelectorAll(".close").forEach((b) => b.addEventListener("click", () => dlg.close()));

  async function open(card) {
    const data = await api(`/api/devices/${card.device.id}/schedule`);
    state = { device: card.device, card, schedule: structuredClone(data.schedule) };
    dlg.querySelector(".dev-name").textContent = card.device.name;
    dlg.querySelector("#sched-enabled").checked = data.schedule.enabled;
    selectedDays = new Set([0, 1, 2, 3, 4]);
    renderDayPicker();
    renderWeek();
    renderProtect();
    renderPending(data.pending);
    renderEvents(data.events);
    err.hidden = true;
    dlg.showModal();
  }

  return { open, pendingText };
})();
