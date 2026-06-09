import { useState } from 'react'
import {
  Card, Stack, Group, Text, Badge, Button, Code, TextInput, Divider, Collapse,
} from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listPrDrafts, queuePrDraft, decidePrDraft, type PrDraft } from './api'

const statusColor: Record<string, string> = {
  'pending-review': 'yellow', approved: 'green', rejected: 'red',
}

// §12.6 gated-PR review queue: approve/reject a draft, then run its push steps
// yourself (personal identity). This UI NEVER pushes.
export function ReviewQueue() {
  const qc = useQueryClient()
  const drafts = useQuery({ queryKey: ['pr-drafts'], queryFn: listPrDrafts, refetchInterval: 3000 })
  const [fid, setFid] = useState('')
  const [target, setTarget] = useState('')
  const [openSteps, setOpenSteps] = useState<string | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: ['pr-drafts'] })

  async function queue() {
    if (!fid.trim()) return
    try { await queuePrDraft(fid.trim(), target.trim() || undefined); setFid(''); refresh() }
    catch (e) { alert(String(e)) }       // 409 surfaces the keeper blockers
  }
  async function decide(id: string, d: 'approved' | 'rejected') {
    try { await decidePrDraft(id, d); refresh() } catch (e) { alert(String(e)) }
  }

  const list = drafts.data || []
  return (
    <Stack gap="sm">
      <Text fw={600}>Review queue — gated PR drafts</Text>
      <Text size="xs" c="dimmed">
        Approve a draft, then run its push steps yourself under the personal identity.
        This screen never pushes (hard gate).
      </Text>
      <Group align="end" gap="xs">
        <TextInput label="Queue finding" placeholder="finding id (e.g. py-1)" value={fid}
                   onChange={(e) => setFid(e.currentTarget.value)} size="xs" style={{ flex: 1 }} />
        <TextInput label="Target" placeholder="jackson-databind" value={target}
                   onChange={(e) => setTarget(e.currentTarget.value)} size="xs" style={{ width: 180 }} />
        <Button size="xs" onClick={queue}>Queue for review</Button>
      </Group>
      <Divider />
      {list.length === 0 && <Text size="sm" c="dimmed">No PR drafts yet. Queue a fixed finding above.</Text>}
      {list.map((d: PrDraft) => (
        <Card key={d.finding_id} withBorder padding="sm">
          <Group justify="space-between" wrap="nowrap">
            <Group gap="xs">
              <Badge color={statusColor[d.status] || 'gray'}>{d.status}</Badge>
              <Text fw={600} size="sm">{d.title}</Text>
            </Group>
            <Text size="xs" c="dimmed" ff="monospace">{d.finding_id} → {d.upstream || d.target}</Text>
          </Group>
          <Text size="xs" ff="monospace" c="dimmed" mt={4}>branch: {d.branch}</Text>
          {d.blockers && d.blockers.length > 0 && (
            <Text size="xs" c="red" mt={4}>blockers: {d.blockers.join('; ')}</Text>
          )}
          <Group gap="xs" mt="xs">
            {d.status === 'pending-review' && (
              <>
                <Button size="xs" color="green" onClick={() => decide(d.finding_id, 'approved')}>Approve</Button>
                <Button size="xs" color="red" variant="light" onClick={() => decide(d.finding_id, 'rejected')}>Reject</Button>
              </>
            )}
            <Button size="xs" variant="subtle"
                    onClick={() => setOpenSteps(openSteps === d.finding_id ? null : d.finding_id)}>
              {openSteps === d.finding_id ? 'Hide' : 'Show'} push steps
            </Button>
          </Group>
          <Collapse in={openSteps === d.finding_id}>
            <Text size="xs" c="dimmed" mt="xs">
              {d.status === 'approved'
                ? 'Approved — run these yourself (personal identity):'
                : 'Manual, identity-gated push steps (run after approving):'}
            </Text>
            <Code block mt={4} style={{ whiteSpace: 'pre-wrap', fontSize: 11 }}>
              {(d.manual_steps || []).join('\n')}
            </Code>
          </Collapse>
        </Card>
      ))}
    </Stack>
  )
}
