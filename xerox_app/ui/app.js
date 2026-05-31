const state = {
  mode: 'page',
  selectedJobId: null,
  activeTimer: null,
  historyTimer: null,
  historyOpen: false,
};

const refs = {
  appRoot: document.getElementById('cobalt'),
  inputContainer: document.getElementById('input-container'),
  clearButton: document.getElementById('clear-button'),
  historyToggle: document.getElementById('historyToggle'),
  historyCount: document.getElementById('historyCount'),
  historyBackdrop: document.getElementById('historyBackdrop'),
  historyDrawer: document.getElementById('historyDrawer'),
  historyClose: document.getElementById('historyClose'),
  modeStrip: document.getElementById('modeStrip'),
  jobForm: document.getElementById('jobForm'),
  urlInput: document.getElementById('link-area'),
  runButton: document.getElementById('download-button'),
  downloadState: document.getElementById('download-state'),
  historyTable: document.getElementById('historyTable'),
  historyList: document.getElementById('historyList'),
  historyEmpty: document.getElementById('historyEmpty'),
  emptyState: document.getElementById('emptyState'),
  jobWorkspace: document.getElementById('jobWorkspace'),
  jobSummary: document.getElementById('jobSummary'),
  openFolderButton: document.getElementById('openFolderButton'),
  openSiteButton: document.getElementById('openSiteButton'),
  foundLinksLink: document.getElementById('foundLinksLink'),
  logsBox: document.getElementById('logsBox'),
};

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const rawText = await response.text();
    let message = rawText;
    try {
      const parsed = JSON.parse(rawText);
      message = parsed.detail || parsed.error || rawText;
    } catch (error) {
      void error;
    }
    throw new Error(message || `Request failed: ${response.status}`);
  }
  return response.json();
}

function fmtDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function statusLabel(status) {
  return status || 'unknown';
}

function escapeHtml(value) {
  return String(value || '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function shortText(value, max = 84) {
  if (!value) return '—';
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function fmtHistoryTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function syncInputUi() {
  const hasValue = Boolean(refs.urlInput.value.trim());
  refs.inputContainer.classList.toggle('clear-visible', hasValue);
  refs.clearButton.classList.toggle('is-hidden', !hasValue);
}

function syncFocusUi(isFocused) {
  refs.inputContainer.classList.toggle('focused', isFocused);
}

function setMode(mode) {
  state.mode = mode;
  refs.modeStrip.querySelectorAll('[data-mode]').forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function setHistoryOpen(isOpen) {
  state.historyOpen = isOpen;
  refs.historyDrawer.classList.toggle('is-open', isOpen);
  refs.historyDrawer.setAttribute('aria-hidden', String(!isOpen));
  refs.historyBackdrop.classList.toggle('is-hidden', !isOpen);
  refs.historyToggle.setAttribute('aria-expanded', String(isOpen));
}

function setButtonEnabled(button, enabled) {
  button.disabled = !enabled;
  button.style.pointerEvents = enabled ? 'auto' : 'none';
  button.style.opacity = enabled ? '1' : '0.45';
}

function isMeaningfulError(errorText) {
  return Boolean(errorText && errorText !== 'Clone process exited with a non-zero code.');
}

function renderJob(job) {
  refs.appRoot.classList.add('has-job');
  refs.emptyState.classList.add('is-hidden');
  refs.jobWorkspace.classList.remove('is-hidden');

  const primaryUrl = job.final_url || job.requested_url || '—';
  const timeLabel = fmtDate(job.created_at);
  const metaParts = [timeLabel];
  if (job.status === 'failed' && isMeaningfulError(job.error)) {
    metaParts.push(job.error);
  }
  refs.jobSummary.innerHTML = `
    <div class="xerox-job-summary-url" title="${escapeHtml(primaryUrl)}">${escapeHtml(primaryUrl)}</div>
    <div class="xerox-job-summary-meta">${escapeHtml(metaParts.join(' • '))}</div>
  `;
  refs.logsBox.textContent = job.logs_text || 'No logs yet.';

  if (job.entry_file) {
    const entryUrl = `/outputs/${job.id}/${job.entry_file}`;
    setButtonEnabled(refs.openSiteButton, true);
    refs.openSiteButton.onclick = () => {
      const opened = window.open(entryUrl, '_blank', 'noopener,noreferrer');
      if (!opened) {
        window.location.href = entryUrl;
      }
    };
  } else {
    setButtonEnabled(refs.openSiteButton, false);
    refs.openSiteButton.onclick = null;
  }

  refs.foundLinksLink.href = `/api/jobs/${job.id}/found-links.txt`;
  refs.foundLinksLink.style.pointerEvents = 'auto';
  refs.foundLinksLink.style.opacity = '1';

  refs.openFolderButton.onclick = () => postAction(`/api/jobs/${job.id}/open-folder`);
}

function renderEmptySelection() {
  refs.appRoot.classList.remove('has-job');
  refs.jobWorkspace.classList.add('is-hidden');
  refs.emptyState.classList.remove('is-hidden');
}

async function postAction(url) {
  try {
    await fetchJson(url, { method: 'POST' });
  } catch (error) {
    window.alert(String(error));
  }
}

function renderHistory(items) {
  refs.historyCount.textContent = String(items.length);
  refs.historyList.innerHTML = '';

  const hasItems = items.length > 0;
  refs.historyEmpty.classList.toggle('is-hidden', hasItems);
  refs.historyTable.classList.toggle('is-hidden', !hasItems);

  if (!hasItems) {
    if (!state.selectedJobId) {
      renderEmptySelection();
    }
    return;
  }

  for (const item of items) {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'xerox-history-item';
    if (item.id === state.selectedJobId) row.classList.add('active');
    const url = item.final_url || item.requested_url || '—';
    row.innerHTML = `
      <div class="xerox-history-type">
        <span class="xerox-history-type-pill">${escapeHtml(item.mode || 'page')}</span>
      </div>
      <div class="xerox-history-url" title="${escapeHtml(url)}">${escapeHtml(url)}</div>
      <div class="xerox-history-time">${escapeHtml(fmtHistoryTime(item.created_at))}</div>
    `;
    row.addEventListener('click', async () => {
      await selectJob(item.id);
      setHistoryOpen(false);
    });
    refs.historyList.appendChild(row);
  }
}

async function loadHistory() {
  const payload = await fetchJson('/api/history');
  renderHistory(payload.items);

  if (!state.selectedJobId && payload.items.length) {
    state.selectedJobId = payload.items[0].id;
    await loadSelectedJob();
  }
}

async function loadSelectedJob() {
  if (!state.selectedJobId) return;
  const job = await fetchJson(`/api/jobs/${state.selectedJobId}`);
  renderJob(job);

  if (state.activeTimer) {
    clearTimeout(state.activeTimer);
    state.activeTimer = null;
  }

  if (job.status === 'running' || job.status === 'queued') {
    state.activeTimer = setTimeout(loadSelectedJob, 1200);
  }
}

async function selectJob(jobId) {
  state.selectedJobId = jobId;
  await loadHistory();
  await loadSelectedJob();
}

async function createJob(event) {
  event.preventDefault();
  const url = refs.urlInput.value.trim();
  if (!url) return;

  refs.runButton.disabled = true;
  refs.runButton.setAttribute('aria-busy', 'true');
  try {
    const job = await fetchJson('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, mode: state.mode }),
    });
    state.selectedJobId = job.id;
    await loadHistory();
    await loadSelectedJob();
  } catch (error) {
    window.alert(String(error));
  } finally {
    refs.runButton.disabled = false;
    refs.runButton.removeAttribute('aria-busy');
  }
}

function bindEvents() {
  refs.modeStrip.addEventListener('click', (event) => {
    const button = event.target.closest('[data-mode]');
    if (!button) return;
    setMode(button.dataset.mode);
  });

  refs.clearButton.addEventListener('click', () => {
    refs.urlInput.value = '';
    refs.urlInput.focus();
    syncInputUi();
  });

  refs.urlInput.addEventListener('input', syncInputUi);
  refs.urlInput.addEventListener('focus', () => syncFocusUi(true));
  refs.urlInput.addEventListener('blur', () => syncFocusUi(false));
  refs.jobForm.addEventListener('submit', createJob);
  refs.historyToggle.addEventListener('click', () => setHistoryOpen(!state.historyOpen));
  refs.historyClose.addEventListener('click', () => setHistoryOpen(false));
  refs.historyBackdrop.addEventListener('click', () => setHistoryOpen(false));
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state.historyOpen) {
      setHistoryOpen(false);
    }
  });
}

async function boot() {
  bindEvents();
  setMode(state.mode);
  syncInputUi();
  await loadHistory();
  state.historyTimer = setInterval(loadHistory, 3000);
}

boot().catch((error) => {
  console.error(error);
  window.alert(String(error));
});
