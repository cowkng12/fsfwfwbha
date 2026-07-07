import type { Catalog, FilterState, GiftTraitCatalog, Listing } from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? '';
export const ACCESS_DENIED_MESSAGE = 'Вы не внесены в белый список бота.';

type TelegramWindow = Window & {
  Telegram?: {
    WebApp?: {
      initData?: string;
    };
  };
};

async function apiFetch(path: string, fallbackError: string, init?: RequestInit) {
  const headers = new Headers(init?.headers);
  const initData = telegramInitData();
  if (initData) headers.set('X-Telegram-Init-Data', initData);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!response.ok) throw new Error(await errorMessage(response, fallbackError));
  return response;
}

function telegramInitData() {
  const webAppData = (window as TelegramWindow).Telegram?.WebApp?.initData;
  if (webAppData) return webAppData;
  for (const value of [window.location.hash.slice(1), window.location.search.slice(1)]) {
    const initData = new URLSearchParams(value).get('tgWebAppData');
    if (initData) return initData;
  }
  return '';
}

async function errorMessage(response: Response, fallback: string) {
  try {
    const data = await response.json();
    return typeof data.detail === 'string' ? data.detail : fallback;
  } catch {
    return response.status === 403 ? ACCESS_DENIED_MESSAGE : fallback;
  }
}

export async function fetchCatalog(): Promise<Catalog> {
  const response = await apiFetch('/api/catalog', 'Cannot load catalog');
  return response.json();
}

export async function fetchGiftTraits(collectionName: string): Promise<GiftTraitCatalog> {
  const params = new URLSearchParams({ collectionName });
  const response = await apiFetch(`/api/catalog/traits?${params.toString()}`, 'Cannot load gift traits');
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
  const response = await apiFetch(`/api/results?${params.toString()}`, 'Cannot load results');
  return response.json();
}

export async function clearListings(): Promise<{ deleted: number; archived: boolean }> {
  const response = await apiFetch('/api/listings/clear?confirm=true', 'Cannot clear listings', { method: 'POST' });
  return response.json();
}
