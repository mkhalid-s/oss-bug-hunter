import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Stack, Group, Text, Badge, Code, Button, Divider, Loader, ScrollArea, Title, Collapse,
} from '@mantine/core'
import CodeMirror from '@uiw/react-codemirror'
import { java } from '@codemirror/lang-java'
import { oneDark } from '@codemirror/theme-one-dark'
import { html as diffHtml } from 'diff2html'
import 'diff2html/bundles/css/diff2html.min.css'
import { getFinding, createRun, findingRunParams } from './api'
import { PrPreviewPanel } from './PrPreviewPanel'

function GateBadge({ label, status }: { label: string; status: any }) {
  const map: Record<string, string> = {
    pass: 'green', fail: 'red', 'not-attempted': 'gray', true: 'red', false: 'green',
  }
  const s = String(status ?? '—')
  return <Badge size="sm" variant="light" color={map[s] || 'gray'}>{label}: {s}</Badge>
}

export function FindingDetail({ id, onRun }: { id: string; onRun: (runId: string) => void }) {
  const qc = useQueryClient()
  const [showPr, setShowPr] = useState(false)
  const q = useQuery({ queryKey: ['finding', id], queryFn: () => getFinding(id) })
  if (q.isError) return <Text c="red">Failed to load finding: {String((q.error as Error)?.message ?? q.error)}</Text>
  if (q.isLoading || !q.data) return <Loader />
  const d = q.data
  const g = d.gates_full || {}

  async function start(kind: string) {
    try {
      const runId = await createRun(kind, findingRunParams(d))
      onRun(runId)
      qc.invalidateQueries({ queryKey: ['runs'] })
      qc.invalidateQueries({ queryKey: ['findings'] })
    } catch (e) {
      // eslint-disable-next-line no-alert
      alert(String(e))
    }
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={4}>{d.id} <Badge variant="light">{d.angle}</Badge></Title>
        <Group gap="xs">
          <Button size="xs" variant="light" onClick={() => start('validate-repro')}>Reproduce</Button>
          <Button size="xs" onClick={() => start('orchestrate')}>Orchestrate</Button>
          <Button size="xs" variant="default" onClick={() => setShowPr((v) => !v)}>
            {showPr ? 'Hide PR' : 'Open PR'}
          </Button>
        </Group>
      </Group>
      <Text>{d.summary}</Text>
      <Text size="sm" ff="monospace" c="dimmed">{d.location}</Text>
      <Group gap="xs">
        <GateBadge label="reproducer" status={(g.reproducer || {}).status} />
        <GateBadge label="fix" status={(g.fix_passes_tests || {}).status} />
        <GateBadge label="dupe" status={(g.dedup || {}).is_duplicate} />
        <Badge size="sm" variant="light">CWE: {(g.cwe || {}).cwe || '—'}</Badge>
        <Badge size="sm" color="orange" variant="light">{d.final_status}</Badge>
      </Group>

      <Collapse in={showPr}>
        <Divider label="Open PR — preview + identity gate" mb="xs" />
        {showPr && <PrPreviewPanel id={id} />}
      </Collapse>

      <Divider label="Evidence" />
      <Code block style={{ whiteSpace: 'pre-wrap' }}>{d.evidence}</Code>

      <Divider label="Reproducer" />
      {d.reproducer_src
        ? <CodeMirror value={d.reproducer_src} extensions={[java()]} theme={oneDark} editable={false} height="320px" />
        : <Text size="sm" c="dimmed">no reproducer</Text>}

      <Divider label="Fix patch" />
      {d.patch_text
        ? (
          <ScrollArea.Autosize mah={360}>
            <div dangerouslySetInnerHTML={{
              __html: diffHtml(d.patch_text, { drawFileList: false, outputFormat: 'line-by-line' }),
            }} />
          </ScrollArea.Autosize>
        )
        : <Text size="sm" c="dimmed">no patch</Text>}
    </Stack>
  )
}
