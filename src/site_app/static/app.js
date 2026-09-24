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
