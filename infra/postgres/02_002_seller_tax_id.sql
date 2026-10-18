-- 02-invoice-processing/sql/02_002_seller_tax_id.sql
-- Foreign sellers have no Polish NIP, only an EU VAT ID (e.g. DE123456789, 11 chars).
-- With seller_nip char(10) they would be stored as NULL, and UNIQUE treats NULLs as
-- distinct, so duplicate foreign invoices would slip through.
-- One column for both: NIP for Polish sellers, VAT ID for foreign ones.
-- The UNIQUE (seller, invoice_number) constraint follows the renamed column.

ALTER TABLE business.invoices RENAME COLUMN seller_nip TO seller_tax_id;
ALTER TABLE business.invoices ALTER COLUMN seller_tax_id TYPE text;
