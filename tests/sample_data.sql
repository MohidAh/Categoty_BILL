-- BillBook v3.0 Sample Data — produces exact invariant values
-- Revenue: 15,150 | Paid: 10,150 | Credit: 5,500 | COGS: 7,490
-- Stock: A=39, B=17, C=2, D=-3 | Payables: 7,500
-- All sales dated 2026-08-11 for Z-report testing

-- Categories (IDs 1-4)
INSERT INTO price_categories (id, name, code, sell_price, color, sort_order, active) VALUES
  (1, 'Budget', 'A', 250, '#3b82f6', 1, 1),
  (2, 'Standard', 'B', 500, '#10b981', 2, 1),
  (3, 'Premium', 'C', 750, '#f59e0b', 3, 1),
  (4, 'Luxury', 'D', 1000, '#ef4444', 4, 1);

-- Suppliers
INSERT INTO suppliers (id, name, phone, address, notes) VALUES
  (1, 'ABC Trading', '0423555666', 'Lahore', 'Wholesale toys'),
  (2, 'XYZ Imports', '0213555777', 'Karachi', 'Cosmetics');

-- Bills (confirmed)
-- Bill 1: paid, A:50pcs @ cost=80 → 4,000
INSERT INTO bills (id, supplier_id, supplier_name, bill_date, bill_no, written_total, computed_total, status, payment_status, created_at) VALUES
  (1, 1, 'ABC Trading', '2026-08-10', 'B001', 4000, 4000, 'confirmed', 'paid', '2026-08-10 10:00:00');
INSERT INTO bill_items (bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) VALUES
  (1, 1, 'Budget Items', 'A', 80, 50, 'pcs', 4000, 1);

-- Bill 2: credit, B:30pcs @ cost=200 → 6,000 + C:5pcs @ cost=300 → 1,500 = 7,500
INSERT INTO bills (id, supplier_id, supplier_name, bill_date, bill_no, written_total, computed_total, status, payment_status, created_at) VALUES
  (2, 2, 'XYZ Imports', '2026-08-10', 'B002', 7500, 7500, 'confirmed', 'credit', '2026-08-10 11:00:00');
INSERT INTO bill_items (bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) VALUES
  (2, 2, 'Standard Items', 'B', 200, 30, 'pcs', 6000, 1),
  (2, 3, 'Premium Items', 'C', 300, 5, 'pcs', 1500, 1);

-- Bill 3: paid, C:5pcs @ cost=300 → 1,500 + D:5pcs @ cost=150 → 750 = 2,250
INSERT INTO bills (id, supplier_id, supplier_name, bill_date, bill_no, written_total, computed_total, status, payment_status, created_at) VALUES
  (3, 1, 'ABC Trading', '2026-08-10', 'B003', 2250, 2250, 'confirmed', 'paid', '2026-08-10 12:00:00');
INSERT INTO bill_items (bill_id, category_id, raw, item_code, price, qty, unit, line_total, page_no) VALUES
  (3, 3, 'Premium Items', 'C', 300, 5, 'pcs', 1500, 1),
  (3, 4, 'Luxury Items', 'D', 150, 5, 'pcs', 750, 1);

-- Purchased totals: A=50, B=30, C=10, D=5

-- Sales (dated 2026-08-11 for Z-report)
-- Sale 1: paid, total=10,150
--   Items: A=11(cost=80), B=5(cost=200), C=4(cost=300), D=3(cost=201.25)
INSERT INTO sales (id, invoice_no, customer_name, customer_phone, subtotal, discount, total, payment_method, payment_status, created_at, tax_rate, tax_amount) VALUES
  (1, 'INV-001', 'Walk-in Customer', '', 10150, 0, 10150, 'cash', 'paid', '2026-08-11 14:00:00', 0, 0);
INSERT INTO sale_items (sale_id, category_id, category_code, item_name, sell_price, cost_price, qty, line_total) VALUES
  (1, 1, 'A', 'Budget Item', 250, 80, 11, 2750),
  (1, 2, 'B', 'Standard Item', 500, 200, 5, 2500),
  (1, 3, 'C', 'Premium Item', 850, 300, 4, 3400),
  (1, 4, 'D', 'Luxury Item', 500, 201.25, 3, 1500);

-- Sale 2: credit, total=5,500
--   Items: B=8(cost=200), C=4(cost=300), D=5(cost=201.25)
INSERT INTO sales (id, invoice_no, customer_name, customer_phone, subtotal, discount, total, payment_method, payment_status, created_at, tax_rate, tax_amount) VALUES
  (2, 'INV-002', 'Credit Customer', '03005556666', 5500, 0, 5500, 'credit', 'credit', '2026-08-11 15:00:00', 0, 0);
INSERT INTO sale_items (sale_id, category_id, category_code, item_name, sell_price, cost_price, qty, line_total) VALUES
  (2, 2, 'B', 'Standard Item', 500, 200, 8, 4000),
  (2, 3, 'C', 'Premium Item', 375, 300, 4, 1500),
  (2, 4, 'D', 'Luxury Item', 200, 201.25, 5, 1000);

-- Sold totals: A=11, B=13, C=8, D=8
-- COGS = 11*80 + 13*200 + 8*300 + 8*201.25 = 880 + 2600 + 2400 + 1610 = 7,490

-- Stock = purchased - sold:
-- A: 50 - 11 = 39
-- B: 30 - 13 = 17
-- C: 10 - 8 = 2
-- D: 5 - 8 = -3

-- Customers
INSERT INTO customers (id, name, phone, address, loyalty_points, total_spent, total_credit, created_at) VALUES
  (1, 'Walk-in Customer', '', '', 0, 0, 0, '2026-08-10 00:00:00'),
  (2, 'Credit Customer', '03005556666', '', 0, 0, 5500, '2026-08-10 00:00:00');
