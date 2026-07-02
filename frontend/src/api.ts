import type { Catalog, FilterState, Listing } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export async function fetchCatalog(): Promise<Catalog> {
  const response = await fetch(`${API_BASE}/api/catalog`);
  if (!response.ok) throw new Error('Cannot load catalog');
  return response.json();
}

export async function fetchResults(filters: FilterState): Promise<{ items: Listing[]; last_research_at: string | null }> {
  const params = new URLSearchParams({ limit: '80' });
  filters.nfts.forEach((value) => params.append('collectionNames', value));
  filters.backdrops.forEach((value) => params.append('backdropNames', value));
  filters.models.forEach((value) => params.append('modelNames', value));
  const response = await fetch(`${API_BASE}/api/results?${params.toString()}`);
  if (!response.ok) throw new Error('Cannot load results');
  return response.json();
}
