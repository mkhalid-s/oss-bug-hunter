import { useQuery } from '@tanstack/react-query'
import { Alert, Stack, Group, Text, Badge, Code, List, Loader, Divider } from '@mantine/core'
import { getPrPreview } from './api'

// READ-ONLY: shows the PR that WOULD be opened + the identity gate. Never pushes
// — opening the PR is a manual, identity-confirmed step (the hard gate).
export function PrPreviewPanel({ id }: { id: string }) {
  const q = useQuery({ queryKey: ['pr', id], queryFn: () => getPrPreview(id) })
  if (q.isError) return <Text c="red">Failed to load PR preview: {String((q.error as Error)?.message ?? q.error)}</Text>
  if (q.isLoading || !q.data) return <Loader size="sm" />
  const p = q.data
  const id_ = p.identity
  const identOk = id_.is_personal && !id_.gh_token_set   // safe to PR to a public repo?
  return (
    <Stack gap="xs">
      {p.ready ? (
        <Alert color="green" title="Ready to open a PR">
          All gates pass. Run the manual steps below (this tool never auto-pushes).
        </Alert>
      ) : (
        <Alert color="red" title="Blocked — do not open this PR yet">
          <List size="sm" spacing={2}>
            {p.blockers.map((b, i) => <List.Item key={i}>{b}</List.Item>)}
          </List>
        </Alert>
      )}

      <Group gap="xs">
        <Text size="sm" fw={600}>GitHub identity:</Text>
        <Badge color={identOk ? 'green' : 'red'} variant="light">
          {id_.active_account || 'unknown'}{id_.is_personal ? ' (personal)' : ' (NOT personal)'}
        </Badge>
        {id_.gh_token_set && <Badge color="red" variant="light">GH_TOKEN set → enterprise pin</Badge>}
        <Text size="xs" c="dimmed">{id_.git_user} &lt;{id_.git_email}&gt;</Text>
      </Group>

      <Group gap="xs">
        <Badge variant="light">upstream: {p.upstream || '—'}</Badge>
        <Badge variant="light">fork: {p.fork}</Badge>
        <Badge variant="light">branch: {p.branch}</Badge>
      </Group>

      <Divider label="PR title" />
      <Code>{p.title}</Code>
      <Divider label="PR body" />
      <Code block style={{ whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto' }}>{p.body}</Code>
      <Divider label="Manual steps (run after confirming the personal account)" />
      <Code block style={{ whiteSpace: 'pre-wrap' }}>{p.manual_steps.join('\n')}</Code>
      <Text size="xs" c="dimmed">{p.note}</Text>
    </Stack>
  )
}
