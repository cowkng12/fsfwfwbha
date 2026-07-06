import { useState } from 'react';
import type { ReactNode } from 'react';
import type { BackdropCatalogItem, Catalog, FilterState, ModelCatalogItem } from '../types';

type Props = {
  catalog: Catalog;
  filters: FilterState;
  symbols: string[];
  onClose: () => void;
  onApply: (filters: FilterState) => void;
};

type SingleKey = 'nfts' | 'models' | 'backdrops' | 'symbols';

export function GiftPickerSheet({ catalog, filters, symbols, onClose, onApply }: Props) {
  const [draft, setDraft] = useState<FilterState>(filters);

  const setSingle = (key: SingleKey, value: string) => {
    setDraft((current) => ({ ...current, [key]: value ? [value] : [] }));
  };

  const setText = (key: 'number' | 'minPrice' | 'maxPrice', value: string) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const clear = () => setDraft({ nfts: [], models: [], backdrops: [], symbols: [], number: '', minPrice: '', maxPrice: '' });

  return (
    <div className="picker-sheet" role="dialog" aria-modal="true" aria-label="Выбрать подарок">
      <div className="picker-panel">
        <button className="sheet-close" onClick={onClose} aria-label="Закрыть">⌄</button>
        <h2>Выбрать подарок</h2>

        <Field label="Подарок">
          <select value={draft.nfts[0] ?? ''} onChange={(event) => setSingle('nfts', event.target.value)}>
            <option value="">Выберите подарок</option>
            {catalog.nfts.map((item) => <option key={item.id || item.name} value={item.name}>{item.name}</option>)}
          </select>
        </Field>

        <Field label="Номер подарка">
          <input value={draft.number} onChange={(event) => setText('number', digitsOnly(event.target.value))} inputMode="numeric" placeholder="Введите номер подарка" />
        </Field>

        <h3>Дополнительные параметры</h3>

        <Field label="Модель">
          <select value={draft.models[0] ?? ''} onChange={(event) => setSingle('models', event.target.value)}>
            <option value="">Выберите модель</option>
            {catalog.models.map((item) => <option key={item.name} value={item.name}>{traitLabel(item)}</option>)}
          </select>
        </Field>

        <Field label="Символ">
          <select value={draft.symbols[0] ?? ''} onChange={(event) => setSingle('symbols', event.target.value)}>
            <option value="">Выберите символ</option>
            {symbols.map((symbol) => <option key={symbol} value={symbol}>{symbol}</option>)}
          </select>
        </Field>

        <Field label="Фон">
          <select value={draft.backdrops[0] ?? ''} onChange={(event) => setSingle('backdrops', event.target.value)}>
            <option value="">Выберите фон</option>
            {catalog.backdrops.map((item) => <option key={item.name} value={item.name}>{traitLabel(item)}</option>)}
          </select>
        </Field>

        <h3>Цена</h3>

        <Field label="От">
          <input value={draft.minPrice} onChange={(event) => setText('minPrice', priceOnly(event.target.value))} inputMode="decimal" placeholder="Минимальная цена" />
        </Field>

        <Field label="До">
          <input value={draft.maxPrice} onChange={(event) => setText('maxPrice', priceOnly(event.target.value))} inputMode="decimal" placeholder="Максимальная цена" />
        </Field>

        <label className="picker-check">
          <input type="checkbox" checked readOnly />
          <span>Только листинги MRKT</span>
        </label>

        <footer className="picker-actions">
          <button className="secondary" onClick={clear}>Очистить</button>
          <button className="primary" onClick={() => onApply(draft)}>Показать</button>
        </footer>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="picker-field"><span>{label}</span>{children}</label>;
}

function traitLabel(item: ModelCatalogItem | BackdropCatalogItem) {
  return `${item.name} (${item.rarity}%)`;
}

function digitsOnly(value: string) {
  return value.replace(/\D/g, '').slice(0, 8);
}

function priceOnly(value: string) {
  return value.replace(',', '.').replace(/[^\d.]/g, '').replace(/(\..*)\./g, '$1').slice(0, 10);
}
