import { useEffect, useMemo, useState } from 'react';
import { fetchResults } from './api';
import type { Listing } from './types';
import { ResultGrid } from './components/ResultGrid';

export function App() {
  const [items, setItems] = useState<Listing[]>([]);
  const [lastResearchAt, setLastResearchAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');

  useEffect(() => {
    let ignore = false;
    const load = () => fetchResults()
      .then((data) => {
        if (!ignore) {
          setItems(data.items);
          setLastResearchAt(data.last_research_at);
        }
      })
      .catch(console.error)
      .finally(() => !ignore && setLoading(false));
    setLoading(true);
    load();
    const timer = window.setInterval(load, 15000);
    return () => {
      ignore = true;
      window.clearInterval(timer);
    };
  }, []);

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return items;
    return items.filter((item) => [item.collection_name, item.model_name, item.backdrop_name, item.number]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(normalized));
  }, [items, query]);

  return (
    <main className="app-shell">
      <section className="profile-card">
        <div className="profile-title">
          <span className="gem">💎</span>
          <b>Премиум</b>
        </div>
        <div className="meter-row"><span>📋 Листинг: {items.length} / 500</span><i style={{ width: `${Math.min(items.length / 5, 100)}%` }} /></div>
        <div className="meter-row muted"><span>💰 Продажа: 0 / 500</span><i /></div>
        <div className="meter-row muted"><span>🔄 Аренда: 0 / 500</span><i /></div>
      </section>

      <nav className="listing-tabs">
        <button className="active">Листинг</button>
        <button>Продажа</button>
        <button>Сдано в аренду</button>
      </nav>

      <ResultGrid items={visibleItems} loading={loading} />

      <footer className="footer-note">
        {lastResearchAt ? `Обновлено ${new Date(lastResearchAt).toLocaleTimeString()}` : 'Ожидание первого ресерча'}
      </footer>

      <div className="bottom-dock">
        <div className="avatar">D</div>
        <label className="dock-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" /></label>
        <button className="add-button">+</button>
      </div>
    </main>
  );
}
