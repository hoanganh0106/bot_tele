# Existing pricing contract

## Production facts (read-only inspection)
Server 54.169.17.170 stores /home/ubuntu/ctv-bot-data/bot_data.json (mode 0600), service ctv-bot, config /home/ubuntu/ctv-bot/config.env. Database contains 62 price_deltas, 11 local products, 9 custom_prices_usdt, no legacy custom_prices; settings.default_markup_fixed is 30000. Do not write real database or credentials to repository.

## Rust pricing
Customer product-selection and order-creation paths and admin product details use base_price + price_deltas[key], or for no override local base unchanged, otherwise settings.default_markup_fixed (Rust default 10000). API products base is supplier DTO price. Local custom_products.price is a stored base, not a verified supplier acquisition cost. Local products replace supplier items on key collision in catalog merge. Name overrides do not affect source identity.

CatalogAggregator currently computes ProductItem.price using constant DEFAULT_MARKUP_FIXED=10000 instead of settings.default_markup_fixed. Export should label the customer/admin selling price as selling_price_vnd and may expose catalog_price_vnd/pricing_mismatch. Do not silently fix Rust behavior in this tool task.

USDT explicit custom_prices_usdt overrides are independent of VND markup and remain unchanged. custom_prices is legacy and unused by the Rust price calculation. Changing target selling VND translates into delta = target - current supplier/local base, preserving dynamic pricing afterward.

## Operations
In-memory DbStore is authoritative; never edit JSON while service runs. Stop, await inactive, reread and validate latest state, back up, mutate selected price_deltas only, atomic replace, restart and verify. Preserve all raw unknown keys by working on parsed JSON, not reserializing through Rust DatabaseData.

## Supplier queries
CTV GET /api/dealer/stock, X-API-KEY; success=true/products map of {name,price,stock}. Hypervin GET /api/products, X-API-Key; success=true/products list of {id,name,price,stock}; Rust prefixes hv_ and truncates finite f64 price to i64. Tools must not issue purchases, debit wallets or fetch balances unnecessarily.
