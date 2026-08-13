// Chat streaming + suggestion follow-up handling.
// External file so CSP 'script-src self' permits it even when htmx swaps the
// body (inline nonces mismatch across swaps).
//
// Live feed: intercepts #chat-form submit, POSTs to the SSE endpoint via fetch,
// parses the text/event-stream incrementally, renders thinking bubbles and
// answer tokens live, then swaps in the server-rendered turn fragment on done.
// On budget_exhausted, offers "Keep looking" (re-runs with continue=1) or
// "Stop" (renders the fallback answer).
(function () {
  if (window.__rpgChatBound) return;
  window.__rpgChatBound = true;

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

  // Document-level delegation survives htmx body swaps.
  document.addEventListener('click', function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('.rpg-suggestion-btn') : null;
    if (btn) askFollowUp(btn);
  });

  // ── Live streaming feed ──────────────────────────────────────────────
  var chatForm = document.getElementById('chat-form');
  if (!chatForm) return;

  function appendEl(parent, tag, cls, text) {
    var el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    parent.appendChild(el);
    return el;
  }

  function pendingTurn(question) {
    var log = document.getElementById('chat-log');
    var user = appendEl(log, 'div', 'rpg-chat-turn rpg-chat-user');
    appendEl(user, 'div', 'role', '\u{1F64B} You');
    appendEl(user, 'p', null, question);
    var agent = appendEl(log, 'div', 'rpg-chat-turn rpg-chat-agent');
    appendEl(agent, 'div', 'role', '\u{1F520} Answer');
    var activity = appendEl(agent, 'div', 'rpg-activity-log');
    var preview = appendEl(agent, 'div', 'rpg-token-preview');
    var spinner = appendEl(agent, 'div', 'rpg-stream-spinner', 'Thinking\u2026');
    return { user: user, agent: agent, activity: activity, preview: preview, spinner: spinner };
  }

  function plainStep(step) {
    var tool = step.tool || '';
    var args = step.args || {};
    switch (tool) {
      case 'fts_search':
        return 'Looking up \u201C' + (args.query || '') + '\u201D\u2026';
      case 'read_file': {
        var p = args.path || '';
        var name = p.split('/').pop().replace(/_/g, ' ').replace('.md', '');
        return 'Reading \u201C' + name + '\u201D\u2026';
      }
      case 'grep':
        return 'Searching for \u201C' + (args.pattern || '') + '\u201D\u2026';
      case 'list_index':
        return 'Browsing the book index\u2026';
      case 'calc':
        return 'Calculating\u2026';
      case 'table_extract':
        return 'Extracting table data\u2026';
      default:
        return 'Working\u2026';
    }
  }

  function renderSteps(el, steps) {
    (steps || []).forEach(function (s) {
      appendEl(el, 'div', 'rpg-log-item', plainStep(s));
    });
  }

  // Parse an SSE stream incrementally from a fetch response body reader.
  function streamFromResponse(response, onEvent, onDone) {
    var reader = response.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';
    if (typeof onDone !== 'function') onDone = function () {};

    function handleLine(line) {
      if (line.indexOf('data: ') !== 0) return;
      var payload = line.slice(6);
      var ev;
      try { ev = JSON.parse(payload); } catch (e) { return; }
      onEvent(ev);
    }

    function pump() {
      return reader.read().then(function (res) {
        if (res.done) { onDone(); return; }
        buffer += decoder.decode(res.value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop();
        lines.forEach(handleLine);
        return pump();
      });
    }
    return pump();
  }

  function submitStreaming(question, csrf, isContinue) {
    var ui = pendingTurn(question);
    var spinner = ui.spinner;
    var streamUrl = chatForm.getAttribute('action').replace(/\/+$/, '') + '/stream';
    var body = new FormData();
    body.append('question', question);
    body.append('_csrf', csrf);
    if (isContinue) body.append('continue', '1');

    return fetch(streamUrl, { method: 'POST', body: body, headers: { 'Accept': 'text/event-stream' } })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return streamFromResponse(resp, function (ev) {
          switch (ev.type) {
            case 'thinking':
              if (spinner && spinner.parentNode) spinner.parentNode.removeChild(spinner);
              appendEl(ui.activity, 'div', 'rpg-log-item', ev.message);
              break;
            case 'token':
              if (spinner && spinner.parentNode) spinner.parentNode.removeChild(spinner);
              ui.preview.textContent += ev.content;
              break;
            case 'done':
              if (ui.agent.parentNode) {
                var frag = document.createElement('template');
                frag.innerHTML = ev.html || '';
                // Fragment renders both user + agent turns; remove the
                // optimistic user div, then replace the agent div with it.
                if (ui.user.parentNode) ui.user.parentNode.removeChild(ui.user);
                ui.agent.replaceWith(frag.content);
              }
              break;
            case 'budget_exhausted':
              if (spinner && spinner.parentNode) spinner.parentNode.removeChild(spinner);
              renderSteps(ui.activity, ev.steps);
              var row = appendEl(ui.agent, 'div', 'rpg-budget-row');
              appendEl(row, 'div', 'rpg-budget-note',
                'The assistant has been searching for a while. Keep going, or stop and answer with what it has found?');
              var keep = appendEl(row, 'button', 'rpg-btn', 'Keep looking');
              keep.type = 'button';
              keep.addEventListener('click', function () {
                row.parentNode.removeChild(row);
                var s = appendEl(ui.agent, 'div', 'rpg-stream-spinner', 'Thinking\u2026');
                submitStreaming(question, csrf, true).then(null, function (err) {
                  s.textContent = 'Something went wrong: ' + err.message;
                });
              });
              var stop = appendEl(row, 'button', 'rpg-btn-secondary', 'Stop, answer with what you have');
              stop.type = 'button';
              stop.addEventListener('click', function () {
                row.parentNode.removeChild(row);
                var fallback = ev.fallback_answer || 'I could not find an answer.';
                var s = appendEl(ui.agent, 'div', 'rpg-stream-spinner', 'Finishing\u2026');
                // Persist the fallback as the final turn — no re-run.
                var fb = new FormData();
                fb.append('question', question);
                fb.append('fallback_answer', fallback);
                fb.append('_csrf', csrf);
                return fetch(chatForm.getAttribute('action') + '/stop', { method: 'POST', body: fb })
                  .then(function (r) { return r.text(); })
                  .then(function (html) {
                    s.parentNode.removeChild(s);
                    if (ui.user.parentNode) ui.user.parentNode.removeChild(ui.user);
                    var frag = document.createElement('template');
                    frag.innerHTML = html;
                    ui.agent.replaceWith(frag.content);
                  })
                  .catch(function () {
                    s.textContent = fallback;
                  });
              });
              break;
          }
        });
      })
      .then(function () {
        // stream complete; if spinner still there, drop it
        if (spinner && spinner.parentNode) spinner.parentNode.removeChild(spinner);
      });
  }

  var htmxFallback = false;
  chatForm.addEventListener('submit', function (e) {
    var input = chatForm.querySelector('input[name="question"]');
    var csrfInput = chatForm.querySelector('input[name="_csrf"]');
    var q = (input && input.value || '').trim();
    var csrf = csrfInput ? csrfInput.value : '';
    if (!q || !csrf) return; // let htmx fallback handle validation
    if (htmxFallback) { htmxFallback = false; return; } // let htmx do its thing
    // Capture phase + stopImmediatePropagation so htmx's document-level
    // delegation never sees this submit (no double-posting).
    e.preventDefault();
    e.stopImmediatePropagation();
    input.value = '';
    var submitBtn = chatForm.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    submitStreaming(q, csrf, false).then(null, function (err) {
      if (submitBtn) submitBtn.disabled = false;
      // Fall back to the standard htmx POST so the question still gets asked.
      // Tear down the optimistic user+agent turn pair so htmx's fresh turn
      // doesn't duplicate them.
      input.value = q;
      var log = document.getElementById('chat-log');
      if (log) {
        var turns = log.querySelectorAll('.rpg-chat-turn');
        for (var i = Math.max(0, turns.length - 2); i < turns.length; i++) {
          if (turns[i].parentNode) turns[i].parentNode.removeChild(turns[i]);
        }
      }
      htmxFallback = true;
      if (window.htmx) htmx.trigger(chatForm, 'submit');
      else chatForm.submit();
    });
  }, true);
})();
