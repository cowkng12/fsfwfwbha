import { useEffect, useMemo, useState } from 'react';
import { ACCESS_DENIED_MESSAGE, clearListings, emptyFilters, fetchCatalog, fetchResults } from './api';
import type { Catalog, FilterState, Listing } from './types';
import { ResultGrid } from './components/ResultGrid';
import { GiftPickerSheet } from './components/GiftPickerSheet';

export function App() {
  const [items, setItems] = useState<Listing[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [filters, setFilters] = useState<FilterState>(emptyFilters);
  const [lastResearchAt, setLastResearchAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);

  const handleError = (error: unknown) => {
    if (error instanceof Error && error.message === ACCESS_DENIED_MESSAGE) {
      setAccessDenied(true);
      return;
    }
    setLoadError('Не получилось загрузить листинги. Открой бота заново или попробуй позже.');
    console.error(error);
  };

  useEffect(() => {
    fetchCatalog().then(setCatalog).catch(handleError);
  }, []);

  useEffect(() => {
    let ignore = false;
    const load = () => fetchResults(filters)
      .then((data) => {
        if (!ignore) {
          setLoadError(null);
          setItems(data.items);
          setLastResearchAt(data.last_research_at);
        }
      })
      .catch(handleError)
      .finally(() => !ignore && setLoading(false));
    setLoading(true);
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      ignore = true;
      window.clearInterval(timer);
    };
  }, [filters]);

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return items;
    return items.filter((item) => [item.collection_name, item.model_name, item.backdrop_name, item.symbol_name, item.number]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(normalized));
  }, [items, query]);

  const symbols = useMemo(() => {
    const values = [...items.map((item) => item.symbol_name), ...filters.symbols]
      .filter((value): value is string => Boolean(value));
    return Array.from(new Set(values)).sort((a, b) => a.localeCompare(b));
  }, [items, filters.symbols]);

  const activeFilterCount = [filters.nfts, filters.models, filters.symbols, filters.backdrops]
    .filter((value) => value.length > 0).length
    + [filters.number, filters.minPrice, filters.maxPrice].filter(Boolean).length;

  const applyFilters = (nextFilters: FilterState) => {
    setFilters(nextFilters);
    setPickerOpen(false);
  };

  const clearFeed = async () => {
    const confirmed = window.confirm('Очистить текущую ленту? Уже найденные лоты будут добавлены в историю, чтобы бот не прислал их повторно.');
    if (!confirmed) return;
    try {
      await clearListings();
    } catch (error) {
      handleError(error);
      return;
    }
    setItems([]);
    setLastResearchAt(null);
  };

  if (accessDenied) {
    return (
      <main className="app-shell access-shell">
        <section className="access-message">{ACCESS_DENIED_MESSAGE}</section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <section className="profile-card">
        <div className="meter-row"><span>📋 Листинг: {items.length} / 500</span><i style={{ width: `${Math.min(items.length / 5, 100)}%` }} /></div>
        <div className="budget-row">Бюджет: до 10 TON</div>
      </section>

      <div className="section-head">
        <div className="section-title">Листинг</div>
        <button className="picker-button" onClick={() => setPickerOpen(true)}>Выбрать подарок{activeFilterCount ? ` · ${activeFilterCount}` : ''}</button>
        <button className="clear-button" onClick={clearFeed}>Очистить</button>
      </div>

      <ResultGrid items={visibleItems} loading={loading} error={loadError} />

      <footer className="footer-note">
        {lastResearchAt ? `Обновлено ${new Date(lastResearchAt).toLocaleTimeString()}` : 'Ожидание первого ресерча'}
      </footer>

      <div className="bottom-dock">
        <div className="avatar">D</div>
        <label className="dock-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" /></label>
      </div>

      {pickerOpen && catalog && (
        <GiftPickerSheet catalog={catalog} filters={filters} symbols={symbols} onClose={() => setPickerOpen(false)} onApply={applyFilters} />
      )}
    </main>
  );
}
