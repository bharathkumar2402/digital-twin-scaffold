const UUID_RE = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

// The agents' narrative text names assets by id ("Failure at root asset 9601...").
// Show the facility's own asset names instead; an id that is not one of its assets is left
// as it is, so an unexpected id stays visible rather than being silently dressed up.
export function replaceAssetIds(text: string, assetNames: Map<string, string>): string {
  return text.replace(UUID_RE, (id) => assetNames.get(id.toLowerCase()) ?? assetNames.get(id) ?? id);
}
