import { useEffect, useState } from 'react';
import { fetchCatalog, fetchResults } from './api';
import type { Catalog, FilterState, Listing } from './types';
import { FilterSheet } from './components/FilterSheet';
import { ResultGrid } from './components/ResultGrid';

const emptyFilters: FilterState = { nfts: [], backdrops: [], models: [] };

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [filters, setFilters] = useState<FilterState>(emptyFilters);
  const [activeSheet, setActiveSheet] = useState<keyof FilterState | null>(null);
  const [items, setItems] = useState<Listing[]>([]);
  const [lastResearchAt, setLastResearchAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchCatalog().then(setCatalog).catch(console.error);
  }, []);

  useEffect(() => {
    let ignore = false;
    setLoading(true);
    fetchResults(filters)
      .then((data) => {
        if (!ignore) {
          setItems(data.items);
          setLastResearchAt(data.last_research_at);
        }
      })
      .catch(console.error)
      .finally(() => !ignore && setLoading(false));
    return () => {
      ignore = true;
    };
  }, [filters]);

  const applyFilter = (key: keyof FilterState, values: string[]) => {
    setFilters((current) => ({ ...current, [key]: values }));
    setActiveSheet(null);
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">@mrkt research</p>
          <h1>NFT Gifts</h1>
        </div>
        <span className="status-dot" />
      </header>

      <section className="filter-row">
        <button onClick={() => setActiveSheet('nfts')}>NFT <b>{filters.nfts.length || 'All'}</b></button>
        <button onClick={() => setActiveSheet('backdrops')}>Фон <b>{filters.backdrops.length || 'All'}</b></button>
        <button onClick={() => setActiveSheet('models')}>Модель <b>{filters.models.length || 'All'}</b></button>
      </section>

      <ResultGrid catalog={catalog} items={items} loading={loading} />

      <footer className="footer-note">
        Ресерч каждые 3 минуты{lastResearchAt ? ` • ${new Date(lastResearchAt).toLocaleTimeString()}` : ''}
      </footer>

      {catalog && activeSheet && (
        <FilterSheet
          type={activeSheet}
          catalog={catalog}
          selected={filters[activeSheet]}
          onClose={() => setActiveSheet(null)}
          onApply={(values) => applyFilter(activeSheet, values)}
        />
      )}
    </main>
  );
}
