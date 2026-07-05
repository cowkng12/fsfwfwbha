import type { Listing } from '../types';

type Props = { items: Listing[]; loading: boolean };

export function ResultGrid({ items, loading }: Props) {
  const visible = items.filter((item) => item.image_url && item.price > 0);

  if (!visible.length) {
    return <div className="empty">{loading ? 'Идет ресерч MRKT...' : 'Пока нет реальных слотов MRKT по выбранным фильтрам.'}</div>;
  }

  return (
    <section className="grid">
      {visible.map((item, index) => (
        <article className={isFresh(item, index) ? 'nft-card is-new' : 'nft-card'} key={`${item.source}-${item.external_id}`}>
          <div className="nft-art">
            <img src={item.image_url!} alt={item.collection_name} />
          </div>
          <div className="card-body">
            <h2>🎁 {item.collection_name}</h2>
            <p className="trait-line">🎲 {item.model_name || 'Модель не указана'} <mark>{formatPercent(item.model_floor_price, item.price)}</mark></p>
            <p className="trait-line">🖼 {item.backdrop_name || 'Фон не указан'} <mark>{formatPercent(item.floor_price, item.price)}</mark></p>
            <p className="price-line">💎 {formatPrice(item.price)} TON</p>
            <a className="open-link" href={item.marketplace_url ?? '#'}>MRKT #{item.number ?? item.external_id.slice(0, 6)}</a>
          </div>
        </article>
      ))}
    </section>
  );
}

function formatPrice(value: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
}

function formatPercent(floor: number | null | undefined, price: number) {
  if (!floor || !price) return '';
  return `${Math.round((price / floor) * 100)}%`;
}

function isFresh(item: Listing, index: number) {
  if (index < 2) return true;
  if (!item.first_seen_at) return false;
  return Date.now() - new Date(item.first_seen_at).getTime() < 60 * 60 * 1000;
}
