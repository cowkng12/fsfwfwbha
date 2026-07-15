import { useMemo, useState } from 'react';
import type { Catalog, FilterState, Listing, NftCatalogItem } from '../types';

type Props = {
  catalog: Catalog;
  filters: FilterState;
  listings: Listing[];
  onClose: () => void;
  onApply: (filters: FilterState) => void;
};

type GiftStats = {
  floor?: number;
  turnover?: number;
};

export function BudgetSheet({ catalog, filters, listings, onClose, onApply }: Props) {
  const [draft, setDraft] = useState<FilterState>(filters);
  const [giftPickerOpen, setGiftPickerOpen] = useState(false);
  const [query, setQuery] = useState('');
  const stats = useMemo(() => giftStats(listings), [listings]);
  const rows = useMemo(
    () => catalog.nfts.filter((item) => item.name.toLowerCase().includes(query.trim().toLowerCase())),
    [catalog.nfts, query],
  );
  const allVisibleNames = rows.map((item) => item.name);
  const allVisibleSelected = allVisibleNames.length > 0 && allVisibleNames.every((name) => draft.nfts.includes(name));

  const setText = (key: 'minPrice' | 'maxPrice', value: string) => {
    setDraft((current) => ({ ...current, [key]: priceOnly(value) }));
  };

  const toggleGift = (name: string) => {
    setDraft((current) => ({
      ...current,
      nfts: current.nfts.includes(name)
        ? current.nfts.filter((item) => item !== name)
        : [...current.nfts, name],
    }));
  };

  const toggleAllVisible = () => {
    setDraft((current) => {
      const rest = current.nfts.filter((name) => !allVisibleNames.includes(name));
      return { ...current, nfts: allVisibleSelected ? rest : Array.from(new Set([...current.nfts, ...allVisibleNames])) };
    });
  };

  const clearBudget = () => {
    setDraft((current) => ({ ...current, nfts: [], minPrice: '', maxPrice: '' }));
  };

  return (
    <div className="budget-sheet" role="dialog" aria-modal="true" aria-label="Бюджет">
      <section className="budget-panel">
        <button className="sheet-close" onClick={onClose} aria-label="Закрыть">×</button>
        {!giftPickerOpen ? (
          <>
            <h2>Бюджет</h2>
            <div className="budget-range">
              <label>
                <span>От</span>
                <input value={draft.minPrice} onChange={(event) => setText('minPrice', event.target.value)} inputMode="decimal" placeholder="0" />
              </label>
              <label>
                <span>До</span>
                <input value={draft.maxPrice} onChange={(event) => setText('maxPrice', event.target.value)} inputMode="decimal" placeholder="10" />
              </label>
            </div>
            <button className="gift-scope-button" onClick={() => setGiftPickerOpen(true)}>
              <span>
                <b>Выбрать подарки для поиска</b>
                <small>{giftScopeText(draft.nfts, catalog.nfts.length)}</small>
              </span>
              <strong>{draft.nfts.length || 'Все'}</strong>
            </button>
            <footer className="budget-actions">
              <button className="secondary" onClick={clearBudget}>Очистить</button>
              <button className="primary" onClick={() => onApply(draft)}>Показать</button>
            </footer>
          </>
        ) : (
          <>
            <header className="budget-subhead">
              <button onClick={() => setGiftPickerOpen(false)}>‹</button>
              <h2>Подарки</h2>
            </header>
            <label className="search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" /></label>
            <div className="budget-table-head"><button onClick={toggleAllVisible}>Выбрать все</button><span>Флор</span><span>Оборот</span><i /></div>
            <div className="budget-gift-list">
              {rows.map((item, index) => {
                const itemStats = stats.get(item.name);
                const selected = draft.nfts.includes(item.name);
                return (
                  <button className="budget-gift-item" key={item.id || item.name} onClick={() => toggleGift(item.name)}>
                    <GiftIcon item={item} index={index} />
                    <span className="item-main">
                      <b>{item.name}</b>
                      <small>{selected ? 'в поиске' : 'не выбран'}</small>
                    </span>
                    <small className="floor">{formatTon(itemStats?.floor)}</small>
                    <small className="floor hot">{formatTurnover(itemStats?.turnover)}</small>
                    <i className={selected ? 'check active' : 'check'}>{selected ? '✓' : ''}</i>
                  </button>
                );
              })}
            </div>
            <footer className="budget-actions">
              <button className="secondary" onClick={() => setDraft((current) => ({ ...current, nfts: [] }))}>Очистить все</button>
              <button className="primary" onClick={() => setGiftPickerOpen(false)}>Готово</button>
            </footer>
          </>
        )}
      </section>
    </div>
  );
}

function giftStats(listings: Listing[]) {
  const stats = new Map<string, GiftStats>();
  for (const item of listings) {
    const current = stats.get(item.collection_name) ?? {};
    if (item.floor_price && (!current.floor || item.floor_price < current.floor)) current.floor = item.floor_price;
    if (item.sales_count) current.turnover = (current.turnover ?? 0) + item.sales_count;
    stats.set(item.collection_name, current);
  }
  return stats;
}

function giftScopeText(selected: string[], total: number) {
  if (!selected.length) return `Все подарки из каталога (${total})`;
  if (selected.length === 1) return selected[0];
  return `Выбрано подарков: ${selected.length}`;
}

function GiftIcon({ item, index }: { item: NftCatalogItem; index: number }) {
  if (item.image) return <span className="mini-icon image-icon"><img src={item.image} alt="" /></span>;
  return <span className="mini-icon" style={{ background: gradients[index % gradients.length] }}>{item.name.slice(0, 1)}</span>;
}

function formatTon(value?: number) {
  return value ? `◆ ${value.toFixed(value >= 10 ? 1 : 2)}` : '—';
}

function formatTurnover(value?: number) {
  return value ? String(value) : '—';
}

function priceOnly(value: string) {
  return value.replace(',', '.').replace(/[^\d.]/g, '').replace(/(\..*)\./g, '$1').slice(0, 10);
}

const gradients = ['linear-gradient(135deg,#ff5a3d,#ffd51b)', 'linear-gradient(135deg,#35ff65,#0b7)', 'linear-gradient(135deg,#8cf,#72f)', 'linear-gradient(135deg,#f7b,#fd3)'];
