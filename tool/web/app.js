const REFRESH_MS = 4000;
let currentStepId = null;
let currentArtifact = null;

function $(id) { return document.getElementById(id); }

// P0-7: bearer token is injected into the served HTML as window.AUTH_TOKEN.
// All /api/* fetches include it. Reloads pick up a new token after server restart.
const AUTH_TOKEN = window.AUTH_TOKEN || '';

async function fetchJson(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (url.startsWith('/api/') && AUTH_TOKEN) {
    headers['Authorization'] = `Bearer ${AUTH_TOKEN}`;
  }
  const r = await fetch(url, { ...opts, headers });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`${r.status}: ${text}`);
  }
  return r.json();
}

function toast(msg, kind = 'success', durationMs = 3000) {
  const el = $('toast');
  el.textContent = msg;
  el.className = `show ${kind}`;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = ''; }, durationMs);
}

async function refreshStatus() {
  try {
    const data = await fetchJson('/api/status');
    renderPipeline(data);
    renderProgress(data.progress, data.total);
    if (currentStepId) {
      const step = data.steps.find(s => s.id === currentStepId);
      if (step) renderStepDetail(step);
    }
  } catch (e) {
    toast(`status error: ${e.message}`, 'error');
  }
}

function renderProgress(done, total) {
  $('progress-text').textContent = `${done} / ${total}`;
  $('progress-fill').style.width = `${(done / total * 100).toFixed(1)}%`;
}

function renderPipeline(data) {
  const ul = $('step-list');
  ul.innerHTML = data.steps.map(s => {
    const isDone = s.done;
    const isCursor = s.id === data.cursor;
    const isActive = s.id === currentStepId;
    const marker = isDone ? '[x]' : (isCursor ? '[>]' : '[ ]');
    return `<li class="kind-${s.kind} ${isDone ? 'done' : 'todo'} ${isCursor ? 'cursor' : ''} ${isActive ? 'active' : ''}"
              onclick="selectStep('${s.id}')">
      <code class="marker">${marker}</code>
      <span class="day">D${s.day}</span>
      <span class="title">${escapeHtml(s.title.replace(/^Day \d+: /, ''))}</span>
      <span class="kind">${s.kind}</span>
    </li>`;
  }).join('');
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

async function selectStep(id) {
  currentStepId = id;
  const data = await fetchJson('/api/status');
  const step = data.steps.find(s => s.id === id);
  renderStepDetail(step);
  renderPipeline(data);
}

function renderStepDetail(step) {
  const el = $('step-detail');
  el.className = '';
  let actionsHtml = '';
  if (step.kind === 'auto') {
    actionsHtml = `<button class="primary" onclick="runStep('${step.id}')" ${step.done ? 'disabled' : ''}>
      ${step.done ? '✓ Done' : '▶ Run this step'}
    </button>`;
  } else {
    const links = [];
    if (step.prompt_path) {
      links.push(`<button class="secondary" onclick="viewFile('${step.prompt_path}')">View prompt</button>`);
    }
    if (step.output_path) {
      links.push(`<button class="secondary" onclick="viewFile('${step.output_path}')">View output</button>`);
    }
    if (step.output_dir) {
      links.push(`<code style="font-size:0.78rem">${step.output_dir}/</code>`);
    }
    // Batch fan-out buttons (claude_driver.run_claude_batch under the hood).
    if (step.id === 'day2-runs') {
      links.unshift(`<button class="primary" onclick="runBacktestBatch()">▶▶ Run all backtest agents (parallel)</button>`);
    }
    if (step.id === 'day3-findings') {
      links.unshift(`<button class="primary" onclick="runHuntBatch([['code-quality',1],['edge-case',1]], 'pass-1 hunts')">▶▶ Run both pass-1 hunts (parallel)</button>`);
    }
    if (step.id === 'day4-passes') {
      links.unshift(`<button class="primary" onclick="runHuntBatch(null, 'Day-4 passes')">▶▶ Run all 4 Day-4 passes (parallel)</button>`);
    }
    if (step.id === 'day3-gates') {
      links.unshift(`<button class="secondary" onclick="suggestGates()">Suggest dedup/CWE (advisory)</button>`);
      links.unshift(`<button class="primary" onclick="runOrchestrate()">🔁 Orchestrate (reproduce→fix→retry)</button>`);
      links.unshift(`<button class="primary" onclick="runFixBatch()">▶▶ Build all fixes (parallel)</button>`);
      links.unshift(`<button class="primary" onclick="runReproBatch()">▶▶ Build all reproducers (parallel)</button>`);
    }
    actionsHtml = links.join(' ');
  }
  el.innerHTML = `
    <h3>${escapeHtml(step.title)} ${step.done ? '<span style="color:#2ea043">✓</span>' : ''}</h3>
    <div class="meta">id: <code>${step.id}</code> · day ${step.day} · ${step.kind}</div>
    ${step.instructions ? `<div class="instructions">${escapeHtml(step.instructions)}</div>` : ''}
    <div class="actions">${actionsHtml}</div>
  `;
}

async function runStep(stepId) {
  toast(`Running ${stepId}…`, 'running', 20000);
  try {
    const result = await fetchJson(`/api/run/${stepId}`, { method: 'POST' });
    if (result.returncode === 0) {
      toast(`✓ ${stepId} done in ${result.elapsed_s}s`, 'success');
    } else {
      toast(`✗ ${stepId} exited ${result.returncode}\n${(result.stderr || '').slice(-300)}`, 'error', 8000);
    }
    refreshStatus();
  } catch (e) {
    toast(`✗ ${e.message}`, 'error', 8000);
  }
}

// Generic parallel-batch dispatcher used by all three batch buttons.
// (label, url, body) -> POST, then summarize succeeded/failed in a toast.
async function runBatch(label, url, body) {
  if (!confirm(`Run ${label} in parallel via claude -p? This can take a while and consumes tokens.`)) return;
  toast(`Running ${label} in parallel…`, 'running', 60000);
  try {
    const result = await fetchJson(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const failed = (result.results || []).filter(r => !r.ok);
    if (failed.length === 0) {
      toast(`✓ ${label}: ${result.succeeded}/${result.total} succeeded`, 'success', 6000);
    } else {
      const id = r => r.issue ?? r.finding_id ?? `${r.angle}:pass${r.pass}`;
      const detail = failed.map(r => `${id(r)}: ${r.error || 'failed'}`).join('\n').slice(0, 400);
      toast(`⚠ ${label}: ${result.succeeded}/${result.total} ok, ${failed.length} failed\n${detail}`, 'error', 10000);
    }
    refreshStatus();
  } catch (e) {
    toast(`✗ ${e.message}`, 'error', 8000);
  }
}

function runBacktestBatch() {
  return runBatch('all backtest agents', '/api/subagent/backtest/batch', { max_parallel: 4 });
}
function runHuntBatch(passes, label) {
  return runBatch(label || 'hunt passes', '/api/subagent/hunt/batch',
                  { passes: passes, max_parallel: 4 });
}
function runReproBatch() {
  return runBatch('all reproducers', '/api/subagent/repro/batch', { max_parallel: 4 });
}
function runFixBatch() {
  return runBatch('all fixes', '/api/subagent/fix/batch', { max_parallel: 4 });
}

// Deterministic advisory gate-fill — fast, non-AI, no confirm needed.
async function suggestGates() {
  try {
    const r = await fetchJson('/api/suggest-gates', { method: 'POST' });
    toast(`✓ Auto-filled dedup/CWE on ${r.updated} scaffold(s) — review + confirm`, 'success', 6000);
    refreshStatus();
  } catch (e) {
    toast(`✗ ${e.message}`, 'error', 8000);
  }
}

// Self-correcting loop: summarized by outcome tally (not per-item ok/fail).
async function runOrchestrate() {
  if (!confirm('Run the self-correcting reproduce→fix→retry loop over all findings? Needs Docker; can take a while and consumes tokens.')) return;
  toast('Orchestrating (reproduce→fix→validate→retry)…', 'running', 120000);
  try {
    const r = await fetchJson('/api/orchestrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_fix_attempts: 2 }),
    });
    const tally = Object.entries(r.outcomes || {}).map(([k, v]) => `${k}:${v}`).join(', ');
    const kind = r.fixed > 0 ? 'success' : 'error';
    toast(`Orchestrate: ${r.fixed}/${r.total} fixed — ${tally}`, kind, 10000);
    refreshStatus();
  } catch (e) {
    toast(`✗ ${e.message}`, 'error', 8000);
  }
}

async function loadArtifactTabs() {
  const data = await fetchJson('/api/artifacts');
  const tabs = $('artifact-tabs');
  tabs.innerHTML = data.artifacts.map(a =>
    `<button class="${a.exists ? '' : 'missing'}" data-name="${a.name}" onclick="viewArtifact('${a.name}')">${a.name}${a.exists ? '' : ' ·'}</button>`
  ).join('');
}

async function viewArtifact(name) {
  currentArtifact = name;
  document.querySelectorAll('#artifact-tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.name === name));
  try {
    const data = await fetchJson(`/api/artifact/${name}`);
    $('artifact-content').textContent = data.exists ? data.content : `(${data.path} doesn't exist yet)`;
  } catch (e) {
    $('artifact-content').textContent = `Error: ${e.message}`;
  }
}

function viewFile(absPath) {
  // Map absolute path to artifact name if whitelisted
  const aliasMap = {
    'explore-prompt.md': 'explore-prompt',
    'explore-inventory.md': 'explore-inventory',
    'shortlist.txt': 'shortlist',
    'cell-1-recon.md': 'recon-report',
  };
  const file = absPath.split('/').pop();
  const alias = aliasMap[file];
  if (alias) viewArtifact(alias);
  else toast(`No viewer for ${absPath}`, 'error');
}

loadArtifactTabs();
refreshStatus();
setInterval(refreshStatus, REFRESH_MS);
