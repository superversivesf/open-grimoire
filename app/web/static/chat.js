// Suggestion follow-up handling — external file so CSP 'script-src self'
// permits it even when htmx swaps the body (inline nonces mismatch across swaps).
(function () {
  if (window.__askFollowUpBound) return;
  window.__askFollowUpBound = true;

  function askFollowUp(btn) {
    var question = btn.getAttribute('data-question');
    var form = document.getElementById('chat-form');
    if (!form) return;
    var input = form.querySelector('input[name="question"]');
    var sendBtn = form.querySelector('button[type="submit"]');
    if (!input || !sendBtn) return;
    input.value = question;
    sendBtn.click();
  }

  // CSP nonce-based: suggestion buttons are injected by htmx, so bind via
  // delegation on the chat log instead of inline onclick.
  document.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('.rpg-suggestion-btn') : null;
    if (btn) askFollowUp(btn);
  });
})();
