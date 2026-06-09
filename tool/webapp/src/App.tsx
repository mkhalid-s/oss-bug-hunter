import { useState, lazy, Suspense } from 'react'
import {
  AppShell, Group, Title, Button, Badge, ScrollArea, Stack, Text, Code, Card,
  Divider, SegmentedControl, Modal, Loader,
} from '@mantine/core'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { listRuns, createRun, EC1, type Run } from './api'
import { useRunStream } from './useRunStream'
import { FindingsBoard } from './FindingsBoard'
import { TargetsView } from './TargetsView'
import { ReviewQueue } from './ReviewQueue'

// Lazy: CodeMirror + diff2html ship in a separate chunk, loaded only when a
// finding detail is opened — keeps the initial bundle small (plan §4.1).
const FindingDetail = lazy(() =>
  import('./FindingDetail').then((m) => ({ default: m.FindingDetail })))

const statusColor: Record<string, string> = {
  running: 'yellow', done: 'green', error: 'red', queued: 'gray',
  interrupted: 'orange', validated: 'green', 'fix-failed': 'red',
  'not-reproduced': 'gray', inconclusive: 'orange',
}

export default function App() {
  const qc = useQueryClient()
  const [view, setView] = useState<'runs' | 'findings' | 'targets' | 'review'>('runs')
  const [selected, setSelected] = useState<string | null>(null)
  const [finding, setFinding] = useState<string | null>(null)
  const runs = useQuery({ queryKey: ['runs'], queryFn: listRuns, refetchInterval: 2000 })
  const { lines, done } = useRunStream(selected)

  async function start(kind: string, params: Record<string, unknown> = {}) {
    try {
      const id = await createRun(kind, params)
      setSelected(id)
      setView('runs')
      qc.invalidateQueries({ queryKey: ['runs'] })
    } catch (e) {
      // eslint-disable-next-line no-alert
      alert(String(e))
    }
  }

  function watchRun(id: string) {
    setFinding(null)
    setSelected(id)
    setView('runs')
    qc.invalidateQueries({ queryKey: ['runs'] })
  }

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 340, breakpoint: 'sm' }} padding="md">
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Title order={4}>OSS Bug Hunter</Title>
            <SegmentedControl
              size="xs"
              value={view}
              onChange={(v) => setView(v as 'runs' | 'findings' | 'targets' | 'review')}
              data={[
                { label: 'Runs', value: 'runs' },
                { label: 'Findings', value: 'findings' },
                { label: 'Targets', value: 'targets' },
                { label: 'Review', value: 'review' },
              ]}
            />
          </Group>
          <Group gap="xs">
            <Button size="xs" onClick={() => start('demo')}>Demo</Button>
            <Button size="xs" variant="light" onClick={() => start('validate-repro', EC1)}>Reproduce ec-1</Button>
            <Button size="xs" variant="light" onClick={() => start('orchestrate', EC1)}>Orchestrate ec-1</Button>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="sm">
        <Text fw={600} size="sm" mb="xs">Runs</Text>
        <ScrollArea h="calc(100vh - 110px)">
          <Stack gap="xs">
            {(runs.data || []).map((r: Run) => (
              <Card
                key={r.id}
                withBorder
                padding="xs"
                onClick={() => { setSelected(r.id); setView('runs') }}
                style={{
                  cursor: 'pointer',
                  outline: selected === r.id ? '2px solid var(--mantine-color-blue-5)' : 'none',
                }}
              >
                <Group justify="space-between" wrap="nowrap">
                  <Text size="sm" ff="monospace">{r.kind}</Text>
                  <Badge color={statusColor[r.status] || 'gray'} size="sm">{r.status}</Badge>
                </Group>
                <Text size="xs" c="dimmed" ff="monospace">{r.id}</Text>
              </Card>
            ))}
            {(runs.data || []).length === 0 && <Text size="sm" c="dimmed">No runs yet.</Text>}
          </Stack>
        </ScrollArea>
      </AppShell.Navbar>

      <AppShell.Main>
        {view === 'targets' ? (
          <TargetsView onRun={watchRun} />
        ) : view === 'review' ? (
          <ReviewQueue />
        ) : view === 'findings' ? (
          <FindingsBoard onSelect={setFinding} />
        ) : !selected ? (
          <Text c="dimmed">Start a run (top right) or pick one to watch its live log.</Text>
        ) : (
          <Stack gap="xs">
            <Group>
              <Text fw={600}>Run</Text>
              <Code>{selected}</Code>
              {done && (
                <Badge color={statusColor[done.status] || 'gray'}>
                  {done.status}{done.exit !== undefined ? ` (exit ${done.exit})` : ''}
                </Badge>
              )}
            </Group>
            <Divider />
            <ScrollArea h="calc(100vh - 150px)" type="auto">
              <Code block style={{ whiteSpace: 'pre-wrap' }}>
                {lines.map((l) => l.line).join('\n') || '(waiting for output…)'}
              </Code>
            </ScrollArea>
          </Stack>
        )}
      </AppShell.Main>

      <Modal opened={!!finding} onClose={() => setFinding(null)} size="xl" title="Finding detail">
        {finding && (
          <Suspense fallback={<Loader />}>
            <FindingDetail id={finding} onRun={watchRun} />
          </Suspense>
        )}
      </Modal>
    </AppShell>
  )
}
