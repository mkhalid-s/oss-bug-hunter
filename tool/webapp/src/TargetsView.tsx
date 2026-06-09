import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Card, Text, Badge, Group, Stack, SimpleGrid, Title, TextInput, Checkbox, Button, Divider,
} from '@mantine/core'
import { listTargets, addTarget } from './api'

const langColor: Record<string, string> = {
  java: 'orange', python: 'blue', go: 'cyan', rust: 'red', javascript: 'yellow', unknown: 'gray',
}

export function TargetsView({ onRun }: { onRun: (id: string) => void }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['targets'], queryFn: listTargets, refetchInterval: 4000 })
  const [url, setUrl] = useState('')
  const [sha, setSha] = useState('')
  const [trusted, setTrusted] = useState(false)
  const [busy, setBusy] = useState(false)

  async function add() {
    if (!url.trim()) return
    setBusy(true)
    try {
      const id = await addTarget(url.trim(), sha.trim(), trusted)
      onRun(id)                       // watch the clone stream
      setUrl(''); setSha('')
      qc.invalidateQueries({ queryKey: ['targets'] })
    } catch (e) {
      // eslint-disable-next-line no-alert
      alert(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Stack>
      <Card withBorder padding="md">
        <Title order={6} mb="xs">Add target</Title>
        <Group align="flex-end">
          <TextInput
            style={{ flex: 1 }} label="Repo URL"
            placeholder="https://github.com/org/repo(.git)"
            value={url} onChange={(e) => setUrl(e.currentTarget.value)}
          />
          <TextInput w={150} label="SHA (optional)" value={sha}
                     onChange={(e) => setSha(e.currentTarget.value)} />
          <Checkbox label="trusted" checked={trusted}
                    onChange={(e) => setTrusted(e.currentTarget.checked)} />
          <Button onClick={add} loading={busy} disabled={!url.trim()}>Add</Button>
        </Group>
        <Text size="xs" c="dimmed" mt="xs">
          Cloning streams as a run. Untrusted targets can't use the no-container
          local backend (fail-closed) — flip trusted only for code you vet.
        </Text>
      </Card>

      <Divider label="Targets" />
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {(q.data || []).map((t) => (
          <Card key={t.name} withBorder padding="sm">
            <Group justify="space-between" wrap="nowrap">
              <Text fw={600}>{t.name}</Text>
              <Badge color={langColor[t.language] || 'gray'} variant="light">{t.language}</Badge>
            </Group>
            <Text size="xs" c="dimmed" ff="monospace" mt={4} lineClamp={1}>{t.repo || '(local)'}</Text>
            <Group gap="xs" mt={6}>
              <Badge size="xs" variant="light">sha {t.sha || '—'}</Badge>
              <Badge size="xs" variant="light" color={t.trusted ? 'green' : 'gray'}>
                {t.trusted ? 'trusted' : 'untrusted'}
              </Badge>
            </Group>
          </Card>
        ))}
        {(q.data || []).length === 0 && <Text c="dimmed">No targets. Add one above.</Text>}
      </SimpleGrid>
    </Stack>
  )
}
