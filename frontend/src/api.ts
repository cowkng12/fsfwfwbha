import type { Catalog, FilterState, GiftTraitCatalog, Listing } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export async function fetchCatalog(): Promise<Catalog> {
  const response = await fetch(`${API_BASE}/api/catalog`);
  if (!response.ok) throw new Error('Cannot load catalog');
  return response.json();
}

export async function fetchGiftTraits(collectionName: string): Promise<GiftTraitCatalog> {
  const params = new URLSearchParams({ collectionName });
  const response = await fetch(`${API_BASE}/api/catalog/traits?${params.toString()}`);
  if (!response.ok) throw new Error('Cannot load gift traits');
  return response.json();
}

export const emptyFilters: FilterState = { nfts: [], backdrops: [], models: [], symbols: [], number: '', minPrice: '', maxPrice: '' };

export async function fetchResults(filters: FilterState = emptyFilters): Promise<{ items: Listing[]; last_research_at: string | null }> {
  const params = new URLSearchParams({ limit: '80' });
  filters.nfts.forEach((value) => params.append('collectionNames', value));
  filters.backdrops.forEach((value) => params.append('backdropNames', value));
  filters.models.forEach((value) => params.append('modelNames', value));
  filters.symbols.forEach((value) => params.append('symbolNames', value));
  if (filters.number.trim()) params.set('number', filters.number.trim());
  if (filters.minPrice.trim()) params.set('minPrice', filters.minPrice.trim());
  if (filters.maxPrice.trim()) params.set('maxPrice', filters.maxPrice.trim());
  const response = await fetch(`${API_BASE}/api/results?${params.toString()}`);
  if (!response.ok) throw new Error('Cannot load results');
  return response.json();
}

export async function clearListings(): Promise<{ deleted: number; archived: boolean }> {
  const response = await fetch(`${API_BASE}/api/listings/clear?confirm=true`, { method: 'POST' });
  if (!response.ok) throw new Error('Cannot clear listings');
  return response.json();
}
