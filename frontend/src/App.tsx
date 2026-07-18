import { useEffect, useMemo, useState } from 'react';
import {
  ACCESS_DENIED_MESSAGE,
  clearListings,
  createSubscriptionInvoice,
  emptyFilters,
  fetchCatalog,
  fetchResults,
  fetchSearchPreferences,
  fetchSubscription,
  saveSearchPreferences,
} from './api';
import type { Catalog, FilterState, Listing, SubscriptionPlan, SubscriptionStatus } from './types';
import { ResultGrid } from './components/ResultGrid';
import { GiftPickerSheet } from './components/GiftPickerSheet';
import { BudgetSheet } from './components/BudgetSheet';

const DEFAULT_BUDGET = '10';
const SEARCH_FILTERS_KEY = 'floorhunt.searchFilters.v1';

type TelegramWebApp = {
  openInvoice?: (url: string, callback?: (status: string) => void) => void;
  openLink?: (url: string) => void;
};

export function App() {
  const [items, setItems] = useState<Listing[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [filters, setFilters] = useState<FilterState>(() => readStoredFilters());
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
    fetchSearchPreferences()
      .then((saved) => {
        if (saved.updated_at) {
          setFilters(normalizeFilters(saved));
          return;
        }
        const stored = readStoredFilters();
        if (hasSavedSearchIntent(stored)) {
          saveSearchPreferences(stored).catch((error) => console.error(error));
        }
      })
      .catch((error) => console.error(error));
  }, []);

  useEffect(() => {
    localStorage.setItem(SEARCH_FILTERS_KEY, JSON.stringify(filters));
  }, [filters]);

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
  const activeNav = subscriptionOpen ? 'profile' : budgetOpen ? 'budget' : pickerOpen ? 'gift' : 'listing';

  const applyFilters = (nextFilters: FilterState) => {
    setFilters({ ...nextFilters, maxPrice: nextFilters.maxPrice || DEFAULT_BUDGET });
    setPickerOpen(false);
  };

  const applyBudget = (nextFilters: FilterState) => {
    const normalized = normalizeFilters(nextFilters);
    setFilters(normalized);
    setBudgetOpen(false);
    saveSearchPreferences(normalized).catch((error) => console.error(error));
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
        <div className="profile-top">
          <div>
            <small className="panel-kicker">FloorHunt</small>
            <h1>Лоты</h1>
          </div>
          <button className="subscription-pill" onClick={() => {
            setBudgetOpen(false);
            setPickerOpen(false);
            setSubscriptionOpen(true);
          }}>
            {subscription?.active ? 'Активна' : 'Доступ'}
          </button>
        </div>
        <div className="meter-row"><span>📋 Листинг: {items.length} / 500</span><i style={{ width: `${Math.min(items.length / 5, 100)}%` }} /></div>
        <div className="budget-row">
          <span>{budgetSummary(filters)}</span>
          <small>{activeFilterCount ? `${activeFilterCount} фильтра` : 'Все подарки'}</small>
        </div>
        <label className="top-search">
          <span>⌕</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" />
        </label>
      </section>

      <div className="feed-head">
        <span>
          <b>Листинг</b>
          <small>{lastResearchAt ? `Обновлено ${new Date(lastResearchAt).toLocaleTimeString()}` : 'Ждём свежий ресёрч'}</small>
        </span>
        <button className="clear-button" onClick={clearFeed}>Очистить</button>
      </div>

      <ResultGrid items={visibleItems} loading={loading} error={loadError} />

      <nav className="bottom-nav" aria-label="Навигация">
        <button aria-label="Листинг" className={activeNav === 'listing' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setPickerOpen(false);
          setSubscriptionOpen(false);
        }}>
          <span>L</span>
          <b>Листинг</b>
        </button>
        <button aria-label="Бюджет" className={activeNav === 'budget' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setPickerOpen(false);
          setSubscriptionOpen(false);
          setBudgetOpen(true);
        }}>
          <span>₮</span>
          <b>Бюджет</b>
        </button>
        <button aria-label="Подарок" className={activeNav === 'gift' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setSubscriptionOpen(false);
          setPickerOpen(true);
        }}>
          <span>◇</span>
          <b>Подарок</b>
        </button>
        <button aria-label="Доступ" className={activeNav === 'profile' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setPickerOpen(false);
          setSubscriptionOpen(true);
        }}>
          <span>✓</span>
          <b>Доступ</b>
        </button>
      </nav>

      {pickerOpen && catalog && (
        <GiftPickerSheet catalog={catalog} filters={filters} symbols={symbols} onClose={() => setPickerOpen(false)} onApply={applyFilters} />
      )}

      {budgetOpen && catalog && (
        <BudgetSheet catalog={catalog} filters={filters} onClose={() => setBudgetOpen(false)} onApply={applyBudget} />
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

function readStoredFilters(): FilterState {
  try {
    const stored = localStorage.getItem(SEARCH_FILTERS_KEY);
    if (stored) return normalizeFilters(JSON.parse(stored));
  } catch {
    // Keep defaults when localStorage contains old or broken data.
  }
  return { ...emptyFilters, maxPrice: DEFAULT_BUDGET };
}

function normalizeFilters(value: Partial<FilterState> | null | undefined): FilterState {
  return {
    nfts: Array.isArray(value?.nfts) ? value.nfts.filter(Boolean) : [],
    backdrops: Array.isArray(value?.backdrops) ? value.backdrops.filter(Boolean) : [],
    models: Array.isArray(value?.models) ? value.models.filter(Boolean) : [],
    symbols: Array.isArray(value?.symbols) ? value.symbols.filter(Boolean) : [],
    number: value?.number ?? '',
    minPrice: value?.minPrice ?? '',
    maxPrice: value?.maxPrice || DEFAULT_BUDGET,
  };
}

function hasSavedSearchIntent(filters: FilterState) {
  return Boolean(
    filters.nfts.length
    || filters.backdrops.length
    || filters.models.length
    || filters.symbols.length
    || filters.number
    || filters.minPrice
    || (filters.maxPrice && filters.maxPrice !== DEFAULT_BUDGET),
  );
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
