// Same-origin REST client. FastAPI serves this app under /app and injects the
// per-launch token as window.AUTH_TOKEN; /api/* calls carry it as a Bearer, and
// the SSE stream carries it as ?token= (EventSource can't set headers).
const TOKEN: string = (window as any).AUTH_TOKEN || ''
const H = { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' }

// Parse a response, surfacing the {ok:false, error:{message}} envelope and
// non-JSON/HTTP errors as thrown Errors (so callers/Query show a real message).
async function _json(r: Response): Promise<any> {
  let d: any
  try {
    d = await r.json()
  } catch {
    throw new Error(`HTTP ${r.status} ${r.statusText || ''}`.trim())
  }
  if (!r.ok || (d && d.ok === false)) {
    throw new Error(d?.error?.message || `HTTP ${r.status}`)
  }
  return d
}

export type Run = {
  id: string; kind: string; status: string; exit_code: number | null
  started?: string; finished?: string
}

export async function listRuns(): Promise<Run[]> {
  return (await _json(await fetch('/api/runs', { headers: H }))).runs || []
}
export async function createRun(kind: string, params: Record<string, unknown> = {}): Promise<string> {
  const d = await _json(await fetch('/api/runs', {
    method: 'POST', headers: H, body: JSON.stringify({ kind, params }),
  }))
  return d.run_id
}
export function streamUrl(id: string): string {
  return `/api/runs/${id}/stream?token=${encodeURIComponent(TOKEN)}`
}

// ---- findings (U2) ----
export type Column = 'proposed' | 'reproduced' | 'fixed' | 'pr-ready'
export type FindingSummary = {
  id: string; angle: string; type: string; location: string; summary: string
  language: string; target: string; final_status: string
  gates: { reproducer: string | null; fix: string | null; dedup: boolean | null; cwe: string | null }
  column: Column
}
export type FindingDetail = FindingSummary & {
  evidence: string; reproducer_hint: string
  gates_full: any; self_consistency: any
  reproducer_src: string | null; reproducer_path: string | null
  patch_text: string | null; patch_path: string | null
}

export async function listFindings(): Promise<FindingSummary[]> {
  return (await _json(await fetch('/api/findings', { headers: H }))).findings || []
}
export async function getFinding(id: string): Promise<FindingDetail> {
  return _json(await fetch(`/api/findings/${id}`, { headers: H }))
}

// Run params for a finding, LANGUAGE-AWARE: java -> JUnit FQCN; otherwise the
// adapter derives the selector, so we just pass the id + the right lang/target.
export function findingRunParams(d: FindingDetail) {
  const lang = d.language || 'java'
  const fqcn = lang === 'java'
    ? `com.fasterxml.jackson.databind.repro.Repro_${d.id.replace(/-/g, '_')}`
    : d.id
  return {
    // finding_id routes Orchestrate through the converged engine (scaffold-driven,
    // multi-language, self-correcting) — same path as /api/orchestrate + MCP.
    // validate-repro/fix ignore it and use the explicit fields below.
    finding_id: d.id,
    worktree: `targets/${d.target || 'jackson-databind'}`,
    fqcn,
    test_file: d.reproducer_path || '',
    patch: d.patch_path || '',
    trusted: true,
    network: lang === 'java' ? 'bridge' : 'none',
    lang,
  }
}

// ---- targets (U3) ----
export type Target = {
  name: string; language: string; adapter: string | null; sha: string | null
  repo: string | null; trusted: boolean; is_git: boolean; has_meta: boolean
}
export async function listTargets(): Promise<Target[]> {
  return (await _json(await fetch('/api/targets', { headers: H }))).targets || []
}
export async function addTarget(url: string, sha: string, trusted: boolean): Promise<string> {
  const d = await _json(await fetch('/api/targets', {
    method: 'POST', headers: H, body: JSON.stringify({ url, sha, trusted }),
  }))
  return d.run_id
}

// ---- PR preview (U4, read-only identity gate) ----
export type PrPreview = {
  finding_id: string; target: string; upstream: string | null; fork: string
  branch: string; title: string; commit_message: string; body: string
  keeper: boolean; blockers: string[]
  identity: {
    active_account: string | null; is_personal: boolean; gh_token_set: boolean
    git_user: string | null; git_email: string | null
  }
  ready: boolean; manual_steps: string[]; note: string
}
export async function getPrPreview(id: string): Promise<PrPreview> {
  return _json(await fetch(`/api/findings/${id}/pr-preview`, { headers: H }))
}

// Quick-launch params for the in-scope Java finding (the header demo buttons).
export const EC1 = {
  worktree: 'targets/jackson-databind',
  fqcn: 'com.fasterxml.jackson.databind.repro.Repro_ec_1',
  test_file: 'cell-1/hunt/repros/ec-1.java',
  patch: 'cell-1/hunt/patches/ec-1.patch',
  trusted: true, network: 'bridge', lang: 'java',
}
