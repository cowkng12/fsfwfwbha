import type { CSSProperties, MouseEvent } from 'react';
import type { Listing } from '../types';

type Props = { items: Listing[]; loading: boolean; error?: string | null };
type CoverStyle = CSSProperties & { '--cover-bg': string };
type TelegramWebApp = {
  openTelegramLink?: (url: string) => void;
  openLink?: (url: string) => void;
};

export function ResultGrid({ items, loading, error }: Props) {
  const visible = items.filter((item) => item.image_url && item.price > 0);

  if (error) {
    return <div className="empty">{error}</div>;
  }

  if (!visible.length) {
    return (
      <div className="empty" aria-busy={loading}>
        {!loading && 'Упс, здесь пока что ничего нет, чтобы это исправить выберите нужные вам подарки для поиска в разделе "Фильтры"'}
      </div>
    );
  }

  return (
    <section className="grid">
      {visible.map((item) => {
        const imageUrl = fragmentImageUrl(item) || item.image_url!;
        const visualUrl = item.telegram_url || imageUrl;
        const combo = formatCombo(item);
        const coverStyle: CoverStyle = { '--cover-bg': coverColor(item.backdrop_name || item.collection_name) };
        return (
          <article className={isFresh(item) ? 'nft-card is-new' : 'nft-card'} key={`${item.source}-${item.external_id}`}>
            <div className="nft-art" style={coverStyle}>
              <span className="nft-badge">#{item.number ?? item.external_id.slice(0, 6)}</span>
              <img src={imageUrl} alt={`${item.collection_name} #${item.number ?? ''}`} loading="lazy" />
            </div>
            <div className="card-body">
              <h2>🎁 {item.collection_name}</h2>
              <p className="trait-line">🎲 {item.model_name || 'Модель не указана'}</p>
              <p className="trait-line">🖼 {item.backdrop_name || 'Фон не указан'}</p>
              {combo && <p className="combo-line">🧩 {combo}</p>}
              <p className="price-line">💎 {formatPrice(item.price)} TON</p>
              <div className="card-actions">
                <span className="gift-number">NFT #{item.number ?? item.external_id.slice(0, 6)}</span>
                {item.marketplace_url && <a className="card-link" href={item.marketplace_url} onClick={(event) => openExternal(event, item.marketplace_url!)} target="_blank" rel="noreferrer">MRKT</a>}
                {visualUrl && <a className="card-link" href={visualUrl} onClick={(event) => openExternal(event, visualUrl)} target="_blank" rel="noreferrer">Визуал</a>}
              </div>
            </div>
          </article>
        );
      })}
    </section>
  );
}

function formatPrice(value: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
}

function formatCombo(item: Listing) {
  const parts: string[] = [];
  if (item.combo_listed_count !== null && item.combo_listed_count !== undefined) {
    parts.push(`${formatPrice(item.combo_listed_count)} на рынке`);
  }
  if (item.combo_floor_price) {
    parts.push(`от ${formatPrice(item.combo_floor_price)} TON`);
  }
  return parts.join(' • ');
}

function fragmentImageUrl(item: Listing) {
  const slug = giftSlug(item);
  return slug ? `https://nft.fragment.com/gift/${slug.toLowerCase()}.webp` : null;
}

function giftSlug(item: Listing) {
  if (item.telegram_url) {
    return item.telegram_url.replace(/\/$/, '').split('/').pop() || null;
  }
  if (!item.collection_name || !item.number) return null;
  const collectionSlug = item.collection_name
    .split(' ')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1).toLowerCase())
    .join('')
    .replace(/[^a-zA-Z0-9]/g, '');
  return `${collectionSlug}-${item.number}`;
}

function coverColor(seed: string) {
  const colors = ['#315a6b', '#355f45', '#6c4e3f', '#584a72', '#675f38', '#6b3f59', '#4d5d78'];
  let hash = 0;
  for (const char of seed) hash = (hash * 31 + char.charCodeAt(0)) % colors.length;
  return colors[hash];
}

function openExternal(event: MouseEvent<HTMLAnchorElement>, url: string) {
  event.preventDefault();
  const webApp = (window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp;
  if (webApp?.openTelegramLink && /^https:\/\/t\.me\//.test(url)) {
    webApp.openTelegramLink(url);
    return;
  }
  if (webApp?.openLink) {
    webApp.openLink(url);
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

function isFresh(item: Listing) {
  if (!item.first_seen_at) return false;
  const ageMs = Date.now() - new Date(item.first_seen_at).getTime();
  return ageMs >= 0 && ageMs < 60 * 60 * 1000;
}
