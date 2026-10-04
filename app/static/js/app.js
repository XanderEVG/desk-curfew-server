// Сплит-кнопка «+30 мин»: открытие/закрытие меню значений
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
  // Клик вне меню — закрыть все
  if (!event.target.closest(".dropdown-menu")) {
    document.querySelectorAll(".btn-split.open").forEach((s) => s.classList.remove("open"));
  }
});