export type NftCatalogItem = { id: string; name: string; image: string };
export type BackdropCatalogItem = { name: string; color: string; rarity: number };
export type ModelCatalogItem = { name: string; image: string; rarity: number; floorPrice?: number };

export type Catalog = {
  nfts: NftCatalogItem[];
  backdrops: BackdropCatalogItem[];
  models: ModelCatalogItem[];
};

export type Listing = {
  source: string;
  external_id: string;
  collection_name: string;
  name: string;
  number?: string | null;
  model_name?: string | null;
  backdrop_name?: string | null;
  image_url?: string | null;
  price: number;
  floor_price?: number | null;
  model_floor_price?: number | null;
  sales_count?: number | null;
  uses_count?: number | null;
  uses_total?: number | null;
  current_owner?: string | null;
  original_sender?: string | null;
  original_recipient?: string | null;
  original_gift_at?: string | null;
  last_sale_at?: string | null;
  last_sale_price?: number | null;
  last_sale_currency?: string | null;
  initial_sale_at?: string | null;
  initial_sale_price?: number | null;
  initial_sale_currency?: string | null;
  initial_sale_stars?: number | null;
  received_at?: string | null;
  export_at?: string | null;
  next_resale_at?: string | null;
  next_transfer_at?: string | null;
  deal_score: number;
  marketplace_url?: string | null;
  telegram_url?: string | null;
  first_seen_at?: string | null;
  notified_at?: string | null;
  updated_at: string;
};

export type FilterState = {
  nfts: string[];
  backdrops: string[];
  models: string[];
};
