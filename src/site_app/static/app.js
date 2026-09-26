// Родитель нужен только для статуса «В клее»; выбор строк ведёт пакет.
(() => {
  function sync(select) {
    const parent = select.form.querySelector('[data-domain-parent]');
    if (parent) parent.hidden = select.value !== 'glued';
  }
  document.addEventListener('change', (event) => {
    if (event.target.matches('[data-domain-status] select')) sync(event.target);
  });
  window.addEventListener('pageshow', () => {
    document.querySelectorAll('[data-domain-status] select').forEach(sync);
  });
  document.querySelectorAll('[data-domain-status] select').forEach(sync);
})();
