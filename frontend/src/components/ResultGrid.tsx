import type { Catalog, Listing } from '../types';

type Props = { catalog: Catalog | null; items: Listing[]; loading: boolean };

export function ResultGrid({ catalog, items, loading }: Props) {
  const fallback: Listing[] = catalog?.nfts.slice(0, 9).map((nft, index) => ({
    source: 'demo',
    external_id: nft.id,
    collection_name: nft.name,
    name: nft.name,
    number: String(125000 + index * 731),
    price: [8.06, 11.4, 14.2, 6.8, 9.9, 7.35, 12.5][index] ?? 5,
    floor_price: null,
    deal_score: 0,
    image_url: null,
    marketplace_url: null,
    updated_at: new Date().toISOString()
  })) ?? [];
  const visible = items.length ? items : fallback;
  const imageByName = new Map(catalog?.nfts.map((nft) => [nft.name, nft.image]) ?? []);

  if (loading && !visible.length) return <div className="empty">Загружаем варианты...</div>;

  return (
    <section className="grid">
      {visible.map((item) => (
        <article className="nft-card" key={`${item.source}-${item.external_id}`}>
          <div className="nft-art">
            <img src={item.image_url || imageByName.get(item.collection_name) || '/assets/nft/surge-board.svg'} alt={item.collection_name} />
            <i>♣</i>
            {item.deal_score > 0 && <em>{Math.round(item.deal_score)}%</em>}
          </div>
          <h2>{item.collection_name}</h2>
          <p>#{item.number ?? item.external_id.slice(0, 6)}</p>
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
