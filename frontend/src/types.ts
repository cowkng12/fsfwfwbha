export type NftCatalogItem = { id: string; name: string; image: string; floorPrice?: number | null; volume?: number | null };
export type BackdropCatalogItem = { name: string; color: string; rarity: number };
export type ModelCatalogItem = { name: string; image: string; rarity: number; floorPrice?: number };
export type SymbolCatalogItem = { name: string; rarity: number };

export type Catalog = {
  nfts: NftCatalogItem[];
  backdrops: BackdropCatalogItem[];
  models: ModelCatalogItem[];
};

export type GiftTraitCatalog = {
  models: ModelCatalogItem[];
  backdrops: BackdropCatalogItem[];
  symbols: SymbolCatalogItem[];
};

export type Listing = {
  source: string;
  external_id: string;
  collection_name: string;
  name: string;
  number?: string | null;
  model_name?: string | null;
  backdrop_name?: string | null;
  symbol_name?: string | null;
  image_url?: string | null;
  price: number;
  floor_price?: number | null;
  model_floor_price?: number | null;
  sales_count?: number | null;
  uses_count?: number | null;
  uses_total?: number | null;
  combo_listed_count?: number | null;
  combo_floor_price?: number | null;
  model_last_sale_at?: string | null;
  model_recent_sales?: string | null;
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
  symbols: string[];
  number: string;
  minPrice: string;
  maxPrice: string;
};

export type SearchPreferences = FilterState & {
  updated_at?: string | null;
};

export type SubscriptionPlan = {
  id: string;
  title: string;
  description: string;
  stars: number;
  duration_days?: number | null;
};

export type SubscriptionStatus = {
  active: boolean;
  plan_id?: string | null;
  status: string;
  started_at?: string | null;
  expires_at?: string | null;
  updated_at?: string | null;
  plans: SubscriptionPlan[];
};
