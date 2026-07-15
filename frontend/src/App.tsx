import { useEffect, useMemo, useState } from 'react';
import { ACCESS_DENIED_MESSAGE, clearListings, createSubscriptionInvoice, emptyFilters, fetchCatalog, fetchResults, fetchSubscription } from './api';
import type { Catalog, FilterState, Listing, SubscriptionPlan, SubscriptionStatus } from './types';
import { ResultGrid } from './components/ResultGrid';
import { GiftPickerSheet } from './components/GiftPickerSheet';
import { BudgetSheet } from './components/BudgetSheet';

const DEFAULT_BUDGET = '10';

type TelegramWebApp = {
  openInvoice?: (url: string, callback?: (status: string) => void) => void;
  openLink?: (url: string) => void;
};

export function App() {
  const [items, setItems] = useState<Listing[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [filters, setFilters] = useState<FilterState>({ ...emptyFilters, maxPrice: DEFAULT_BUDGET });
  const [lastResearchAt, setLastResearchAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const [subscriptionOpen, setSubscriptionOpen] = useState(false);
  const [subscription, setSubscription] = useState<SubscriptionStatus | null>(null);
  const [subscriptionBusy, setSubscriptionBusy] = useState<string | null>(null);
  const [subscriptionError, setSubscriptionError] = useState<string | null>(null);

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
    fetchSubscription().then(setSubscription).catch((error) => console.error(error));
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
    + [filters.number, filters.minPrice].filter(Boolean).length
    + (filters.maxPrice && filters.maxPrice !== DEFAULT_BUDGET ? 1 : 0);

  const applyFilters = (nextFilters: FilterState) => {
    setFilters({ ...nextFilters, maxPrice: nextFilters.maxPrice || DEFAULT_BUDGET });
    setPickerOpen(false);
  };

  const applyBudget = (nextFilters: FilterState) => {
    setFilters(nextFilters);
    setBudgetOpen(false);
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

  const reloadSubscription = () => {
    fetchSubscription().then(setSubscription).catch((error) => console.error(error));
  };

  const buySubscription = async (plan: SubscriptionPlan) => {
    setSubscriptionBusy(plan.id);
    setSubscriptionError(null);
    try {
      const invoice = await createSubscriptionInvoice(plan.id);
      const webApp = (window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
      if (webApp?.openInvoice) {
        webApp.openInvoice(invoice.invoice_link, () => reloadSubscription());
      } else if (webApp?.openLink) {
        webApp.openLink(invoice.invoice_link);
      } else {
        window.open(invoice.invoice_link, '_blank', 'noopener,noreferrer');
      }
    } catch (error) {
      console.error(error);
      setSubscriptionError('Не получилось открыть оплату. Попробуй ещё раз.');
    } finally {
      setSubscriptionBusy(null);
    }
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
        <div className="budget-row"><span>{budgetSummary(filters)}</span><small>{giftScopeSummary(filters.nfts, catalog?.nfts.length)}</small></div>
        <button className="subscription-button" onClick={() => setSubscriptionOpen(true)}>
          Моя подписка
          <span>{subscription?.active ? 'активна' : 'не активна'}</span>
        </button>
      </section>

      <div className="section-head">
        <div className="section-title">Листинг</div>
        <button className="budget-button" onClick={() => setBudgetOpen(true)}>Бюджет</button>
        <button className="picker-button" onClick={() => setPickerOpen(true)}>Подарок{activeFilterCount ? ` · ${activeFilterCount}` : ''}</button>
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

      {budgetOpen && catalog && (
        <BudgetSheet catalog={catalog} filters={filters} listings={items} onClose={() => setBudgetOpen(false)} onApply={applyBudget} />
      )}

      {subscriptionOpen && (
        <SubscriptionSheet
          status={subscription}
          busyPlanId={subscriptionBusy}
          error={subscriptionError}
          onClose={() => setSubscriptionOpen(false)}
          onBuy={buySubscription}
        />
      )}
    </main>
  );
}

function budgetSummary(filters: FilterState) {
  if (filters.minPrice && filters.maxPrice) return `Бюджет: ${filters.minPrice}-${filters.maxPrice} TON`;
  if (filters.minPrice) return `Бюджет: от ${filters.minPrice} TON`;
  if (filters.maxPrice) return `Бюджет: до ${filters.maxPrice} TON`;
  return 'Бюджет: без лимита';
}

function giftScopeSummary(selected: string[], total?: number) {
  if (!selected.length) return `Подарки: все${total ? ` (${total})` : ''}`;
  if (selected.length === 1) return `Подарок: ${selected[0]}`;
  return `Подарки: ${selected.length}`;
}

function SubscriptionSheet({
  status,
  busyPlanId,
  error,
  onClose,
  onBuy,
}: {
  status: SubscriptionStatus | null;
  busyPlanId: string | null;
  error: string | null;
  onClose: () => void;
  onBuy: (plan: SubscriptionPlan) => void;
}) {
  const currentPlan = status?.plans.find((plan) => plan.id === status.plan_id);
  return (
    <div className="subscription-sheet">
      <section className="subscription-panel">
        <button className="sheet-close" onClick={onClose}>×</button>
        <h2>Моя подписка</h2>
        <div className={status?.active ? 'subscription-status active' : 'subscription-status'}>
          <b>{status?.active ? 'Активна' : 'Не активна'}</b>
          <span>{subscriptionStatusText(status, currentPlan)}</span>
        </div>
        <div className="plan-list">
          {(status?.plans ?? []).map((plan) => (
            <button className="plan-item" key={plan.id} onClick={() => onBuy(plan)} disabled={Boolean(busyPlanId)}>
              <span>
                <b>{plan.title}</b>
                <small>{plan.description}</small>
              </span>
              <strong>{busyPlanId === plan.id ? '...' : `${plan.stars} ⭐`}</strong>
            </button>
          ))}
        </div>
        {error && <div className="subscription-error">{error}</div>}
      </section>
    </div>
  );
}

function subscriptionStatusText(status: SubscriptionStatus | null, plan?: SubscriptionPlan) {
  if (!status) return 'Загружаем данные';
  if (!status.active) return 'Выбери тариф ниже';
  if (status.status === 'owner') return 'Владелец · навсегда';
  if (!status.expires_at) return `${plan?.title ?? 'Доступ'} · навсегда`;
  return `${plan?.title ?? 'Доступ'} · до ${new Date(status.expires_at).toLocaleDateString()}`;
}
