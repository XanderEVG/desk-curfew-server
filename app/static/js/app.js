// Дропдаун-меню «добавить время»
document.addEventListener("click", (event) => {
  const toggle = event.target.closest(".dropdown-toggle");
  if (toggle) {
    event.stopPropagation();
    const dropdown = toggle.closest(".dropdown");
    document.querySelectorAll(".dropdown.open").forEach((d) => {
      if (d !== dropdown) d.classList.remove("open");
    });
    dropdown.classList.toggle("open");
    return;
  }
  // Клик вне меню — закрыть (пункты меню сами переза рендерят сетку)
  if (!event.target.closest(".dropdown-menu")) {
    document.querySelectorAll(".dropdown.open").forEach((d) => d.classList.remove("open"));
  }
});