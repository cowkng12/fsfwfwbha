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
type AppPage = 'listing' | 'profile';

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
  const [activePage, setActivePage] = useState<AppPage>('listing');
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
    let ignore = false;
    const loadCatalog = () => fetchCatalog()
      .then((data) => {
        if (!ignore) setCatalog(data);
      })
      .catch(handleError);
    loadCatalog();
    const timer = window.setInterval(loadCatalog, 60000);
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
    return () => {
      ignore = true;
      window.clearInterval(timer);
    };
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

  const applyFilters = (nextFilters: FilterState) => {
    const normalized = normalizeFilters({ ...nextFilters, maxPrice: nextFilters.maxPrice || DEFAULT_BUDGET });
    setFilters(normalized);
    setPickerOpen(false);
    saveSearchPreferences(normalized).catch((error) => console.error(error));
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
      {activePage === 'listing' ? (
        <>
          <section className="profile-card listing-panel">
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
            <div className="listing-tools" aria-label="Настройки листинга">
              <button onClick={() => setBudgetOpen(true)}>
                <span>₮</span>
                <b>Бюджет</b>
              </button>
              <button onClick={() => setPickerOpen(true)}>
                <span>◇</span>
                <b>Подарок</b>
              </button>
            </div>
            <label className="top-search">
              <span>⌕</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск" />
            </label>
          </section>

          <div className="feed-head">
            <span>
              <b>Листинги</b>
              <small>{lastResearchAt ? `Обновлено ${new Date(lastResearchAt).toLocaleTimeString()}` : 'Ждём свежий ресёрч'}</small>
            </span>
            <button className="clear-button" onClick={clearFeed}>Очистить</button>
          </div>

          <ResultGrid items={visibleItems} loading={loading} error={loadError} />
        </>
      ) : (
        <ProfilePage status={subscription} user={telegramUser} />
      )}

      <nav className={pickerOpen || budgetOpen ? 'bottom-nav is-covered' : 'bottom-nav'} aria-label="Навигация">
        <button aria-label="Листинги" className={activePage === 'listing' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setPickerOpen(false);
          setActivePage('listing');
        }}>
          <span>L</span>
          <b>Листинги</b>
        </button>
        <button aria-label="Профиль" className={activePage === 'profile' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setPickerOpen(false);
          setActivePage('profile');
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

function ProfilePage({
  status,
  user,
}: {
  status: SubscriptionStatus | null;
  user: TelegramUser | null;
}) {
  const currentPlan = status?.plans.find((plan) => plan.id === status.plan_id);
  const daysLeft = subscriptionDaysLeft(status);
  const action = status?.active ? 'renew' : 'buy';
  return (
    <section className="profile-page" aria-label="Профиль">
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
        {daysLeft !== null && <span>До окончания вашей подписки осталось {daysWord(daysLeft)}</span>}
        {!status?.active && <span>Оформи подписку, чтобы начать поиск</span>}
      </div>
      <button className="contact-button" onClick={() => openSubscriptionContact(action)}>
        {status?.active ? 'Продлить' : 'Приобрести подписку'}
      </button>
    </section>
  );
}

function subscriptionStatusText(status: SubscriptionStatus | null, plan?: SubscriptionPlan) {
  if (!status) return 'Загружаем данные';
  if (!status.active) return 'Неактивна';
  if (status.status === 'owner') return 'Владелец · навсегда';
  if (!status.expires_at) return `${plan?.title ?? 'Доступ'} · навсегда`;
  return `Действительна до ${new Date(status.expires_at).toLocaleDateString()}`;
}

function getTelegramUser(): TelegramUser | null {
  const webApp = (window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
  return webApp?.initDataUnsafe?.user ?? null;
}

function openSubscriptionContact(action: 'buy' | 'renew') {
  const text = subscriptionMessage(action);
  navigator.clipboard?.writeText(text).catch(() => undefined);
  const url = `https://t.me/diamondilya?text=${encodeURIComponent(text)}`;
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

function subscriptionMessage(action: 'buy' | 'renew') {
  return [
    '#подписка',
    action === 'buy' ? 'Привет, хочу купить подписку' : 'Привет, хочу продлить подписку',
    'На день/неделю/месяц/навсегда',
  ].join('\n');
}

function subscriptionDaysLeft(status: SubscriptionStatus | null) {
  if (!status?.active || !status.expires_at || status.status === 'owner') return null;
  const msLeft = new Date(status.expires_at).getTime() - Date.now();
  return Math.max(0, Math.ceil(msLeft / 86_400_000));
}

function daysWord(days: number) {
  const mod10 = days % 10;
  const mod100 = days % 100;
  const word = mod10 === 1 && mod100 !== 11
    ? 'день'
    : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)
      ? 'дня'
      : 'дней';
  return `${days} ${word}`;
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
