// ===== Тосты =====
function showToast(message, type = "success") {
  let container = document.querySelector("#toasts");
  if (!container) {
    container = document.createElement("div");
    container.id = "toasts";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("fade-out");
    setTimeout(() => toast.remove(), 400);
  }, 3500);
}

// Тосты из HTMX (сервер шлёт HX-Trigger: show-toast)
document.body.addEventListener("show-toast", (e) => {
  showToast(e.detail.message, e.detail.type || "success");
});

// Тост после редиректа (body data-toast)
document.addEventListener("DOMContentLoaded", () => {
  const key = document.body.dataset.toast;
  if (!key) return;
  const messages = { settings_saved: ["Настройки сохранены", "success"] };
  const [msg, type] = messages[key] || [key, "info"];
  showToast(msg, type);
  history.replaceState(null, "", location.pathname);
});

// ===== Сплит-кнопка «+30 мин» =====
document.addEventListener("click", (event) => {
  const toggle = event.target.closest(".dropdown-toggle");
  if (toggle) {
    event.stopPropagation();
    const split = toggle.closest(".btn-split");
    document.querySelectorAll(".btn-split.open").forEach((s) => {
      if (s !== split) s.classList.remove("open");
    });
    split.classList.toggle("open");
    return;
  }
  if (!event.target.closest(".dropdown-menu")) {
    document.querySelectorAll(".btn-split.open").forEach((s) => s.classList.remove("open"));
  }
});

// ===== Страница ПК: редактор расписания =====
const WEEK_DAYS = [["mon", "Пн"], ["tue", "Вт"], ["wed", "Ср"], ["thu", "Чт"], ["fri", "Пт"], ["sat", "Сб"], ["sun", "Вс"]];

function slotRowHtml() {
  const days = ["mon", "tue", "wed", "thu", "fri"];
  const dayBoxes = WEEK_DAYS.map(
    ([wd, label]) =>
      `<label class="day"><input type="checkbox" class="slot-day" value="${wd}" ${days.includes(wd) ? "checked" : ""}>${label}</label>`
  ).join("");
  return `<div class="slot-row">
    <div class="slot-days">${dayBoxes}</div>
    <label class="time">с <input type="time" class="slot-from" value="16:00"></label>
    <label class="time">по <input type="time" class="slot-until" value="20:00"></label>
    <label class="day"><input type="checkbox" class="slot-active" checked>активен</label>
    <button type="button" class="secondary slot-remove">✕</button>
  </div>`;
}

document.addEventListener("click", (event) => {
  const editor = document.querySelector("#schedule-editor");
  if (!editor) return;

  if (event.target.closest("#add-slot")) {
    editor.querySelector(".slot-actions").insertAdjacentHTML("beforebegin", slotRowHtml());
    return;
  }

  if (event.target.closest(".slot-remove")) {
    event.target.closest(".slot-row").remove();
    return;
  }

  if (event.target.closest("#save-schedule")) {
    const slots = [];
    editor.querySelectorAll(".slot-row").forEach((row) => {
      const days = [...row.querySelectorAll(".slot-day:checked")].map((c) => c.value);
      const from = row.querySelector(".slot-from").value;
      const until = row.querySelector(".slot-until").value;
      const active = row.querySelector(".slot-active").checked;
      if (days.length && from && until) {
        slots.push({ days, allowed_from: from, allowed_until: until, is_active: active });
      }
    });

    fetch(`/api/pcs/${editor.dataset.pc}/schedule`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slots }),
    }).then((r) => {
      if (r.ok) {
        showToast("Расписание сохранено", "success");
      } else {
        r.json().then((d) => showToast("Ошибка сохранения: " + JSON.stringify(d), "error"));
      }
    });
  }
});