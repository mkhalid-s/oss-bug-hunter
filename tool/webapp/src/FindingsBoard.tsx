import { useQuery } from '@tanstack/react-query'
import { Card, Text, Badge, Group, Stack, SimpleGrid, Title } from '@mantine/core'
import { listFindings } from './api'

const COLS = [
  { key: 'proposed', label: 'Proposed', color: 'gray' },
  { key: 'reproduced', label: 'Reproduced', color: 'yellow' },
  { key: 'fixed', label: 'Fixed', color: 'teal' },
  { key: 'pr-ready', label: 'PR-ready', color: 'green' },
]

export function FindingsBoard({ onSelect }: { onSelect: (id: string) => void }) {
  const q = useQuery({ queryKey: ['findings'], queryFn: listFindings, refetchInterval: 5000 })
  const items = q.data || []
  return (
    <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
      {COLS.map((c) => {
        const col = items.filter((f) => f.column === c.key)
        return (
          <Stack key={c.key} gap="xs">
            <Group justify="space-between">
              <Title order={6}>{c.label}</Title>
              <Badge color={c.color} variant="light">{col.length}</Badge>
            </Group>
            {col.map((f) => (
              <Card key={f.id} withBorder padding="sm" onClick={() => onSelect(f.id)} style={{ cursor: 'pointer' }}>
                <Group justify="space-between" wrap="nowrap">
                  <Text fw={600} size="sm">{f.id}</Text>
                  <Badge size="xs" variant="light">{f.angle}</Badge>
                </Group>
                <Text size="xs" c="dimmed" lineClamp={3}>{f.summary}</Text>
                <Text size="xs" ff="monospace" c="dimmed" mt={4} lineClamp={1}>
                  {f.location?.split('/').pop()}
                </Text>
                {f.final_status && f.final_status !== 'pending' && (
                  <Badge size="xs" color="orange" variant="light" mt={4}>{f.final_status}</Badge>
                )}
              </Card>
            ))}
            {col.length === 0 && <Text size="xs" c="dimmed">—</Text>}
          </Stack>
        )
      })}
    </SimpleGrid>
  )
}
