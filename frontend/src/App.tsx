import { useEffect, useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
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

type SubscriptionOption = {
  id: 'day' | 'week' | 'month' | 'forever';
  title: string;
  messageLine: string;
  price: string;
  caption: string;
};

const SUBSCRIPTION_OPTIONS: SubscriptionOption[] = [
  { id: 'day', title: 'На день', messageLine: 'На день', price: '500р / 4$', caption: 'Быстро попробовать ресерч' },
  { id: 'week', title: 'На неделю', messageLine: 'На неделю', price: '2500р / 30$', caption: 'Для плотного поиска' },
  { id: 'month', title: 'На месяц', messageLine: 'На месяц', price: '4000р / 50$', caption: 'Оптимально для работы' },
  { id: 'forever', title: 'Навсегда', messageLine: 'Навсегда', price: '10000р / 150$', caption: 'Один раз и без продлений' },
];

const FALLING_STARS = [
  { left: '4%', delay: '0s', duration: '18s', size: 8, opacity: 0.75, drift: -12, top: '-10%' },
  { left: '13%', delay: '-5s', duration: '21s', size: 6, opacity: 0.5, drift: 8, top: '-18%' },
  { left: '23%', delay: '-9s', duration: '24s', size: 7, opacity: 0.65, drift: -10, top: '-14%' },
  { left: '34%', delay: '-2s', duration: '20s', size: 5, opacity: 0.55, drift: 6, top: '-22%' },
  { left: '44%', delay: '-11s', duration: '23s', size: 7, opacity: 0.7, drift: -8, top: '-8%' },
  { left: '55%', delay: '-7s', duration: '19s', size: 6, opacity: 0.48, drift: 12, top: '-20%' },
  { left: '63%', delay: '-13s', duration: '26s', size: 8, opacity: 0.62, drift: -6, top: '-15%' },
  { left: '72%', delay: '-4s', duration: '22s', size: 5, opacity: 0.58, drift: 9, top: '-25%' },
  { left: '81%', delay: '-15s', duration: '27s', size: 7, opacity: 0.68, drift: -11, top: '-12%' },
  { left: '89%', delay: '-6s', duration: '20s', size: 6, opacity: 0.52, drift: 7, top: '-19%' },
  { left: '94%', delay: '-12s', duration: '25s', size: 5, opacity: 0.45, drift: -5, top: '-16%' },
  { left: '52%', delay: '-17s', duration: '28s', size: 9, opacity: 0.72, drift: 10, top: '-6%' },
] as const;

export function App() {
  const [items, setItems] = useState<Listing[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [filters, setFilters] = useState<FilterState>(() => readStoredFilters());
  const [lastResearchAt, setLastResearchAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessDenied, setAccessDenied] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
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

  const activeFilterCount = [filters.nfts, filters.models, filters.symbols, filters.backdrops]
    .filter((value) => value.length > 0).length
    + [filters.number, filters.minPrice].filter(Boolean).length
    + (filters.maxPrice && filters.maxPrice !== DEFAULT_BUDGET ? 1 : 0);

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
      <div className="falling-stars" aria-hidden="true">
        {FALLING_STARS.map((star, index) => (
          <span
            key={`${star.left}-${index}`}
            className="falling-star"
            style={{
              left: star.left,
              top: star.top,
              animationDelay: star.delay,
              animationDuration: star.duration,
              fontSize: `${star.size}px`,
              '--drift': `${star.drift}px`,
              '--star-opacity': star.opacity,
            } as CSSProperties}
          >
            ✦
          </span>
        ))}
      </div>
      {activePage === 'listing' ? (
        <>
          <section className="profile-card listing-panel">
            <div className="profile-top">
              <div>
                <h1>Лоты</h1>
              </div>
            </div>
            <div className="meter-row"><span><UiIcon name="list" /> Листинг: {items.length} / 500</span><i style={{ width: `${Math.min(items.length / 5, 100)}%` }} /></div>
            <div className="budget-row">
              <span>{budgetSummary(filters)}</span>
              <small>{activeFilterCount ? `${activeFilterCount} фильтра` : 'Все подарки'}</small>
            </div>
            <div className="listing-tools" aria-label="Настройки листинга">
              <button onClick={() => setBudgetOpen(true)}>
                <span><UiIcon name="sliders" /></span>
                <b>Бюджет</b>
              </button>
              {budgetOpen && catalog && (
                <BudgetSheet catalog={catalog} filters={filters} onClose={() => setBudgetOpen(false)} onApply={applyBudget} />
              )}
            </div>
            <label className="top-search">
              <span><UiIcon name="search" /></span>
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

          <ResultGrid items={visibleItems} loading={loading} error={loadError} subscriptionActive={Boolean(subscription?.active)} />
        </>
      ) : (
        <ProfilePage status={subscription} user={telegramUser} />
      )}

      <nav className={budgetOpen ? 'bottom-nav is-covered' : 'bottom-nav'} aria-label="Навигация">
        <button aria-label="Листинги" className={activePage === 'listing' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setActivePage('listing');
        }}>
          <span><UiIcon name="grid" /></span>
          <b>Листинги</b>
        </button>
        <button aria-label="Профиль" className={activePage === 'profile' ? 'nav-button active' : 'nav-button'} onClick={() => {
          setBudgetOpen(false);
          setActivePage('profile');
        }}>
          <span><UiIcon name="user" /></span>
          <b>Профиль</b>
        </button>
      </nav>

    </main>
  );
}

function UiIcon({ name }: { name: 'list' | 'sliders' | 'search' | 'grid' | 'user' }) {
  const paths = {
    list: (
      <>
        <path d="M8 6h10" />
        <path d="M8 12h10" />
        <path d="M8 18h10" />
        <path d="M4 6h.01" />
        <path d="M4 12h.01" />
        <path d="M4 18h.01" />
      </>
    ),
    sliders: (
      <>
        <path d="M4 7h10" />
        <path d="M18 7h2" />
        <path d="M4 17h2" />
        <path d="M10 17h10" />
        <circle cx="16" cy="7" r="2" />
        <circle cx="8" cy="17" r="2" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="6" />
        <path d="m16 16 4 4" />
      </>
    ),
    grid: (
      <>
        <path d="M4 4h7v7H4z" />
        <path d="M13 4h7v7h-7z" />
        <path d="M4 13h7v7H4z" />
        <path d="M13 13h7v7h-7z" />
      </>
    ),
    user: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M5 20c1.4-4 12.6-4 14 0" />
      </>
    ),
  };
  return (
    <svg className="ui-icon" viewBox="0 0 24 24" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

function budgetSummary(filters: FilterState) {
  if (filters.minPrice && filters.maxPrice) return `Лимит: ${filters.minPrice}-${filters.maxPrice} TON`;
  if (filters.minPrice) return `Лимит: от ${filters.minPrice} TON`;
  if (filters.maxPrice) return `Лимит: до ${filters.maxPrice} TON`;
  return 'Лимит: без ограничений';
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
  const [plansOpen, setPlansOpen] = useState(false);
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
      <button className="contact-button" onClick={() => setPlansOpen(true)}>
        {status?.active ? 'Продлить подписку' : 'Купить подписку'}
      </button>
      {plansOpen && (
        <div className="subscription-sheet" role="dialog" aria-modal="true" aria-label="Выбор подписки" onClick={() => setPlansOpen(false)}>
          <section className="subscription-panel subscription-menu" onClick={(event) => event.stopPropagation()}>
            <button className="sheet-close" onClick={() => setPlansOpen(false)} aria-label="Закрыть">×</button>
            <h2>Выбери подписку</h2>
            <div className="plan-list profile-plan-list">
              {SUBSCRIPTION_OPTIONS.map((option) => (
                <button className="plan-item subscription-plan-card" key={option.id} onClick={() => {
                  setPlansOpen(false);
                  openSubscriptionContact(action, option);
                }}>
                  <span>
                    <b>{option.title}</b>
                    <small>{option.caption}</small>
                  </span>
                  <strong>{option.price}</strong>
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
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

function openSubscriptionContact(action: 'buy' | 'renew', option?: SubscriptionOption) {
  const text = subscriptionMessage(action, option);
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

function subscriptionMessage(action: 'buy' | 'renew', option?: SubscriptionOption) {
  return [
    '#подписка',
    action === 'buy' ? 'Привет, хочу купить подписку' : 'Привет, хочу продлить подписку',
    option?.messageLine ?? 'На день/неделю/месяц/навсегда',
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
