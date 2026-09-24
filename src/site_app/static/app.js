document.addEventListener('submit', (event) => {
  const message = event.target.dataset.confirm;
  if (message && !window.confirm(message)) event.preventDefault();
});

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy-target]');
  if (!button) return;
  const input = document.getElementById(button.dataset.copyTarget);
  if (!input) return;
  try {
    await navigator.clipboard.writeText(input.value);
    button.textContent = 'Скопировано';
  } catch {
    input.focus();
    input.select();
    button.textContent = 'Нажми Ctrl+C';
  }
});

// Мобильное меню брендов: на узком экране сайдбар — выезжающая панель. Кнопка
// [data-sidebar-toggle] открывает, [data-sidebar-close] (фон, ×), Escape и
// переход на широкий экран закрывают. Пока панель открыта, остальная страница
// inert — фокус и скринридер не уходят под подложку. Фокус уходит в панель и
// возвращается на кнопку. На широком экране бургер скрыт стилями, и всё это
// неактивно (элементы просто отсутствуют на страницах без сайдбара — см.
// ранние return ниже).
(function () {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.querySelector("[data-sidebar-toggle]");
  const backdrop = document.querySelector(".sidebar-backdrop");
  if (!sidebar || !toggle || !backdrop) return;
  const behind = document.querySelectorAll(".topbar, .content");

  function setOpen(open, restoreFocus = true) {
    if (document.body.classList.contains("sidebar-open") === open) return;
    document.body.classList.toggle("sidebar-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    backdrop.hidden = !open;
    behind.forEach((el) => { el.inert = open; });
    if (open) sidebar.focus();
    else if (restoreFocus) toggle.focus();
  }

  toggle.addEventListener("click", () => setOpen(!document.body.classList.contains("sidebar-open")));
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-sidebar-close]")) setOpen(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    setOpen(false);
  });
  // Окно стало широким — панель просто закрывается, фокус не переносим на
  // уже скрытый бургер.
  window.matchMedia("(min-width: 861px)").addEventListener("change", (event) => {
    if (event.matches) setOpen(false, false);
  });
})();
