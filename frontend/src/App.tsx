import { useEffect, useMemo, useState } from 'react';
import {
  ACCESS_DENIED_MESSAGE,
  clearListings,
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
  openTelegramLink?: (url: string) => void;
  openLink?: (url: string) => void;
  initDataUnsafe?: {
    user?: TelegramUser;
  };
};

type TelegramUser = {
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
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
  const telegramUser = getTelegramUser();

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
            <h1>Лоты</h1>
          </div>
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
        <button aria-label="Профиль" className={activeNav === 'profile' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setPickerOpen(false);
          setSubscriptionOpen(true);
        }}>
          <span>✓</span>
          <b>Профиль</b>
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
          user={telegramUser}
          onClose={() => setSubscriptionOpen(false)}
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
  user,
  onClose,
}: {
  status: SubscriptionStatus | null;
  user: TelegramUser | null;
  onClose: () => void;
}) {
  const currentPlan = status?.plans.find((plan) => plan.id === status.plan_id);
  return (
    <div className="subscription-sheet" role="dialog" aria-modal="true" aria-label="Профиль">
      <section className="subscription-panel profile-panel">
        <button className="sheet-close" onClick={onClose}>×</button>
        <div className="profile-hero">
          <div className="profile-avatar">
            {user?.photo_url ? <img src={user.photo_url} alt="" /> : userInitials(user)}
          </div>
          <b>{userDisplayName(user)}</b>
          {user?.username && <small>@{user.username}</small>}
        </div>
        <div className="profile-divider" />
        <div className="subscription-heading">
          <h2>Подписка:</h2>
          <span className={status?.active ? 'subscription-state active' : 'subscription-state'}>
            {status?.active ? 'активна' : 'неактивна'}
          </span>
        </div>
        <div className={status?.active ? 'subscription-status active' : 'subscription-status'}>
          <b>{subscriptionStatusText(status, currentPlan)}</b>
          <span>{status?.active ? 'Доступ к поиску лотов включён' : 'Оформи подписку, чтобы начать поиск'}</span>
        </div>
        <button className="contact-button" onClick={openSubscriptionContact}>
          {status?.active ? 'Продлить' : 'Купить'}
        </button>
        <p className="payment-note">Покупка подписки: <b>diamondilya</b></p>
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

function getTelegramUser(): TelegramUser | null {
  const webApp = (window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
  return webApp?.initDataUnsafe?.user ?? null;
}

function openSubscriptionContact() {
  const url = 'https://t.me/diamondilya';
  const webApp = (window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
  if (webApp?.openTelegramLink) {
    webApp.openTelegramLink(url);
    return;
  }
  if (webApp?.openLink) {
    webApp.openLink(url);
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

function userDisplayName(user: TelegramUser | null) {
  if (!user) return 'Профиль';
  return [user.first_name, user.last_name].filter(Boolean).join(' ') || user.username || 'Профиль';
}

function userInitials(user: TelegramUser | null) {
  const name = userDisplayName(user);
  return name
    .split(/\s+/)
    .map((part) => part.slice(0, 1))
    .join('')
    .slice(0, 2)
    .toUpperCase();
}
