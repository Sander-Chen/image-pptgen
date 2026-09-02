const ARTIFACT_ROUTE_PREFIX = '/artifacts/';
const RELATIVE_ARTIFACT_PREFIX = 'artifacts/';

export function toArtifactUrl(pathValue: string | undefined | null): string | null {
  if (!pathValue) return null;
  const trimmed = pathValue.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith(ARTIFACT_ROUTE_PREFIX)) return trimmed;
  if (trimmed.startsWith(RELATIVE_ARTIFACT_PREFIX)) return `/${trimmed}`;
  const artifactSegment = trimmed.lastIndexOf(ARTIFACT_ROUTE_PREFIX);
  if (artifactSegment >= 0) return trimmed.slice(artifactSegment);
  return `${ARTIFACT_ROUTE_PREFIX}${trimmed.replace(/^\/+/, '')}`;
}
