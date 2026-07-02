import type { Listing } from '../types';

type Props = { items: Listing[]; loading: boolean };

export function ResultGrid({ items, loading }: Props) {
  const visible = items.filter((item) => item.image_url && item.price > 0);

  if (!visible.length) {
    return <div className="empty">{loading ? 'Идет ресерч MRKT...' : 'Пока нет реальных слотов MRKT по выбранным фильтрам.'}</div>;
  }

  return (
    <section className="grid">
      {visible.map((item) => (
        <article className="nft-card" key={`${item.source}-${item.external_id}`}>
          <div className="nft-art">
            <img src={item.image_url!} alt={item.collection_name} />
            <i>♣</i>
          </div>
          <h2>{item.collection_name}</h2>
          <p>#{item.number ?? item.external_id.slice(0, 6)}</p>
          <p className="traits">{[item.model_name, item.backdrop_name].filter(Boolean).join(' • ') || 'MRKT slot'}</p>
          <div className="price-row">
            <button>◆ {formatPrice(item.price)}</button>
            <a href={item.marketplace_url ?? '#'} aria-label="open listing">↳</a>
          </div>
        </article>
      ))}
    </section>
  );
}

function formatPrice(value: number) {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(value);
}
