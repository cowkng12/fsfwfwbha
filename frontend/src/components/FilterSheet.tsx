import { useMemo, useState } from 'react';
import type { BackdropCatalogItem, Catalog, FilterState, ModelCatalogItem, NftCatalogItem } from '../types';

type Props = {
  type: keyof FilterState;
  catalog: Catalog;
  selected: string[];
  onClose: () => void;
  onApply: (values: string[]) => void;
};

const titles: Record<keyof FilterState, string> = {
  nfts: 'NFT',
  backdrops: 'Фон',
  models: 'Модель'
};

export function FilterSheet({ type, catalog, selected, onClose, onApply }: Props) {
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState<string[]>(selected);
  const rows = useMemo<Array<NftCatalogItem | BackdropCatalogItem | ModelCatalogItem>>(() => {
    const source = type === 'nfts' ? catalog.nfts : type === 'backdrops' ? catalog.backdrops : catalog.models;
    return source.filter((item) => item.name.toLowerCase().includes(query.toLowerCase()));
  }, [catalog, query, type]);

  const toggle = (name: string) => {
    setDraft((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]));
  };
  const allNames = rows.map((item) => item.name);
  const allSelected = allNames.length > 0 && allNames.every((name) => draft.includes(name));

  return (
    <div className="sheet">
      <div className="sheet-panel">
        <header className="sheet-header">
          <h2>{titles[type]}</h2>
          <button onClick={onClose}>×</button>
        </header>
        <label className="search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" /></label>
        {type === 'nfts' && <div className="table-head"><b>Выбрать все</b><span>Флор</span><span>Оборот</span><i /></div>}
        {type !== 'nfts' && <div className="select-all"><button onClick={() => setDraft(allSelected ? [] : Array.from(new Set([...draft, ...allNames])))}>Выбрать все</button><i /></div>}

        <div className="sheet-list">
          {type === 'nfts' && (
            <button className="nft-select-all" onClick={() => setDraft(allSelected ? [] : Array.from(new Set([...draft, ...allNames])))}>Выбрать все</button>
          )}
          {rows.map((item, index) => (
            <button className="filter-item" key={item.name} onClick={() => toggle(item.name)}>
              <Icon item={item} type={type} index={index} />
              <span className="item-main">
                <b>{item.name}</b>
                {type === 'nfts' && <small>23 июн.</small>}
              </span>
              {'rarity' in item && <mark>{item.rarity}%</mark>}
              {'floorPrice' in item && <small className="floor">◆ {item.floorPrice}</small>}
              {type === 'nfts' && <><small className="floor hot">◆ {sampleFloor(index)}</small><small className="floor">◆ {sampleVolume(index)}</small></>}
              <i className={draft.includes(item.name) ? 'check active' : 'check'} />
            </button>
          ))}
        </div>
        <footer className="sheet-actions">
          <button className="secondary" onClick={() => setDraft([])}>Очистить все</button>
          <button className="primary" onClick={() => onApply(draft)}>Показать результаты</button>
        </footer>
        <p className="brand">@mrkt</p>
      </div>
    </div>
  );
}

function Icon({ item, type, index }: { item: NftCatalogItem | BackdropCatalogItem | ModelCatalogItem; type: keyof FilterState; index: number }) {
  if (type === 'backdrops' && 'color' in item) return <span className="color-icon" style={{ background: `radial-gradient(circle at 30% 20%, #fff4, transparent 35%), ${item.color}` }} />;
  return <span className="mini-icon" style={{ background: gradients[index % gradients.length] }}>{item.name.slice(0, 1)}</span>;
}

const gradients = ['linear-gradient(135deg,#ff5a3d,#ffd51b)', 'linear-gradient(135deg,#35ff65,#0b7)', 'linear-gradient(135deg,#8cf,#72f)', 'linear-gradient(135deg,#f7b,#fd3)'];
const sampleFloor = (index: number) => [8.06, 112.89, 4.03, 6.68, 2.62, 35.07, 3.93, 6.42][index % 8];
const sampleVolume = (index: number) => ['7 104.12', '6 372.83', '1 047.89', '744.95', '733.51', '5 988.91', '626.1', '1 022.48'][index % 8];
