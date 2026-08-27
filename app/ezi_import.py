"""DBF Import Module — reads Ezi POS backup (FoxPro DBF files).
Maps Ezi POS data to BillBook's schema:
  - COMPANY.DBF → shop profile (name, address, NTN, STRN)
  - INVOICE.DBF → sales (invoice_no, date, total, payment_method, status)
  - INVTRANS.DBF → sale_items (item_name, qty, price, line_total, cost)
  - STOCK.DBF → price_categories + inventory items
  - DEBTORS.DBF → customers (name, phone, address, credit_limit, balance)
  - CREDITOR.DBF → suppliers (name, phone, address, balance)
  - BARCODE.DBF → barcodes mapping
"""
import logging
import os
import tempfile
import zipfile
from datetime import datetime
from dbfread import DBF

logger = logging.getLogger(__name__)


def import_ezi_backup(zip_path, db_conn):
    """Import an Ezi POS backup ZIP into BillBook.
    
    Args:
        zip_path: Path to the BU*.zip backup file
        db_conn: BillBook db.conn() context manager
    
    Returns:
        dict with import counts: {sales, items, customers, suppliers, products, shop}
    """
    # Extract DBF files to temp dir
    tmpdir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmpdir)
        
        results = {
            'shop': {}, 'sales': 0, 'sale_items': 0,
            'customers': 0, 'suppliers': 0, 'products': 0,
            'errors': []
        }
        
        # 1. Import shop profile from COMPANY.DBF
        company_path = os.path.join(tmpdir, 'COMPANY.DBF')
        if os.path.exists(company_path):
            try:
                table = DBF(company_path, encoding='latin-1')
                records = list(table)
                if len(records) > 0:
                    rec = records[0]  # First record = active company
                    shop = {
                        'shop_name': (rec.get('NAME1', '') or '').strip(),
                        'address': ' '.join(filter(None, [
                            rec.get('ADDRESS1', ''), rec.get('ADDRESS2', ''),
                            rec.get('ADDRESS3', ''), rec.get('ADDRESS4', ''),
                        ])).strip(),
                        'phone': (rec.get('ADDRESS2', '') or '').strip(),
                        'ntn': (rec.get('NTN', '') or '').strip(),
                        'strn': (rec.get('GST', '') or '').strip(),
                    }
                    results['shop'] = shop
                    with db_conn() as c:
                        for key, val in shop.items():
                            if val:
                                setting_key = f'shop_{key}' if key != 'shop_name' else 'shop_name'
                                c.execute(
                                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                                    "ON CONFLICT(key) DO UPDATE SET value = ?",
                                    (setting_key, val, val)
                                )
            except Exception as e:
                results['errors'].append(f"COMPANY: {e}")
        
        # 2. Import customers from DEBTORS.DBF
        debtors_path = os.path.join(tmpdir, 'DEBTORS.DBF')
        if os.path.exists(debtors_path):
            try:
                table = DBF(debtors_path, encoding='latin-1')
                for rec in table:
                    name = (rec.get('NAME', '') or '').strip()
                    if not name or name == 'Cash Sales':
                        continue  # Skip walk-in
                    phone = (rec.get('MOBILE', '') or rec.get('PHONE', '') or '').strip()
                    address = ' '.join(filter(None, [
                        rec.get('ADDRESS1', ''), rec.get('ADDRESS2', ''),
                        rec.get('SUBURB', ''), rec.get('STATE', ''),
                    ])).strip()
                    credit_limit = float(rec.get('CLIMIT') or 0)
                    balance = float(rec.get('BALANCE') or 0)
                    with db_conn() as c:
                        # Check if customer exists by name
                        existing = c.execute(
                            "SELECT id FROM customers WHERE lower(name) = lower(?)", (name,)
                        ).fetchone()
                        if not existing:
                            c.execute(
                                "INSERT INTO customers(name, phone, address, total_credit, credit_limit) "
                                "VALUES(?,?,?,?,?)",
                                (name, phone, address, balance if balance > 0 else 0, credit_limit)
                            )
                            results['customers'] += 1
            except Exception as e:
                results['errors'].append(f"DEBTORS: {e}")
        
        # 3. Import suppliers from CREDITOR.DBF
        creditor_path = os.path.join(tmpdir, 'CREDITOR.DBF')
        if os.path.exists(creditor_path):
            try:
                table = DBF(creditor_path, encoding='latin-1')
                for rec in table:
                    name = (rec.get('NAME', '') or '').strip()
                    if not name or name == 'Cash Purchase':
                        continue
                    phone = (rec.get('PHONE', '') or rec.get('MOBILE', '') or '').strip()
                    address = ' '.join(filter(None, [
                        rec.get('ADDRESS1', ''), rec.get('ADDRESS2', ''),
                        rec.get('SUBURB', ''), rec.get('STATE', ''),
                    ])).strip()
                    with db_conn() as c:
                        existing = c.execute(
                            "SELECT id FROM suppliers WHERE lower(name) = lower(?)", (name,)
                        ).fetchone()
                        if not existing:
                            c.execute(
                                "INSERT INTO suppliers(name, phone, address) VALUES(?,?,?)",
                                (name, phone, address)
                            )
                            results['suppliers'] += 1
            except Exception as e:
                results['errors'].append(f"CREDITOR: {e}")
        
        # 4. Import stock items from STOCK.DBF
        stock_path = os.path.join(tmpdir, 'STOCK.DBF')
        stock_fpt = os.path.join(tmpdir, 'STOCK.FPT')
        if os.path.exists(stock_path):
            try:
                table = DBF(stock_path, encoding='latin-1')
                for rec in table:
                    part_no = (rec.get('PART_NO', '') or '').strip()
                    desc = (rec.get('DESC', '') or '').strip()
                    if not desc:
                        continue
                    price = float(rec.get('PRICE1') or 0)
                    cost = float(rec.get('COST') or 0)
                    qty = float(rec.get('QTY') or 0)
                    
                    # Map to BillBook price categories
                    # Ezi POS uses price tiers: PRICE1=retail, PRICE2-5=wholesale/vip
                    with db_conn() as c:
                        # Check if category exists by code (= PART_NO or derived from price)
                        # For wholesale shops, items are often grouped by price
                        # We'll create/update price_categories based on price1
                        existing_cat = c.execute(
                            "SELECT id FROM price_categories WHERE code = ?",
                            (part_no[:4] if part_no else str(int(price)) if price else 'GEN',)
                        ).fetchone()
                        
                        if not existing_cat and price > 0:
                            # Create a price category for this item
                            # Group by price ranges
                            code = part_no[:4] if part_no else f"P{int(price)}"
                            c.execute(
                                "INSERT INTO price_categories(name, code, sell_price, sell_wholesale, color, sort_order, active) "
                                "VALUES(?,?,?,?,?,?,1)",
                                (desc[:50], code, price, float(rec.get('PRICE2') or 0),
                                 '#3b82f6', 99)
                            )
                            results['products'] += 1
                        elif existing_cat:
                            results['products'] += 1  # Count as product
            except Exception as e:
                results['errors'].append(f"STOCK: {e}")
        
        # 5. Import sales from INVOICE.DBF + INVTRANS.DBF
        invoice_path = os.path.join(tmpdir, 'INVOICE.DBF')
        invtrans_path = os.path.join(tmpdir, 'INVTRANS.DBF')
        if os.path.exists(invoice_path) and os.path.exists(invtrans_path):
            try:
                invoices = DBF(invoice_path, encoding='latin-1')
                invtrans = DBF(invtrans_path, encoding='latin-1')
                
                # Group line items by invoice number
                items_by_invoice = {}
                for it in invtrans:
                    num = it.get('NUMBER')
                    if num not in items_by_invoice:
                        items_by_invoice[num] = []
                    items_by_invoice[num].append(it)
                
                with db_conn() as c:
                    for inv in invoices:
                        # Skip if already imported (check by invoice_no)
                        inv_no = f"EZI-{inv.get('NUMBER', 0)}"
                        existing = c.execute(
                            "SELECT id FROM sales WHERE invoice_no = ?", (inv_no,)
                        ).fetchone()
                        if existing:
                            continue
                        
                        # Parse date (DBF date is already a string YYYY-MM-DD)
                        date_str = str(inv.get('DATE', ''))
                        # Parse time from ADD_TIME field (format: YYMMDDHHMMSS)
                        add_time = str(inv.get('ADD_TIME', ''))
                        if len(add_time) >= 14:
                            try:
                                hh = add_time[6:8]
                                mm = add_time[8:10]
                                ss = add_time[10:12]
                                created_at = f"{date_str} {hh}:{mm}:{ss}"
                            except Exception:
                                created_at = f"{date_str} 00:00:00"
                        else:
                            created_at = f"{date_str} 00:00:00"
                        
                        total = float(inv.get('AMOUNT') or 0)
                        paid = float(inv.get('PAID') or 0)
                        status = inv.get('STATUS', 'P')
                        pay_term = (inv.get('PAYTERM') or '').strip()
                        
                        # Map payment status
                        if status == 'P' and paid >= total - 0.01:
                            payment_status = 'paid'
                        elif paid > 0:
                            payment_status = 'partial'
                        else:
                            payment_status = 'credit'
                        
                        # Map payment method
                        payment_method = 'cash'  # Default
                        if pay_term and pay_term.lower() not in ('cash', ''):
                            payment_method = pay_term.lower()
                        
                        # Create sale
                        sale_id = c.execute(
                            "INSERT INTO sales(invoice_no, customer_name, customer_phone, "
                            "subtotal, discount, total, payment_method, payment_status, created_at, notes) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (inv_no, 'Ezi POS Import', '', total, 0, total,
                             payment_method, payment_status, created_at,
                             f"Imported from Ezi POS backup on {datetime.now().strftime('%Y-%m-%d')}")
                        ).lastrowid
                        results['sales'] += 1
                        
                        # Import line items
                        for item in items_by_invoice.get(inv.get('NUMBER'), []):
                            if item.get('ISTOTAL'):
                                # This is a summary line, not a real item — skip
                                # Actually in Ezi POS, ISTOTAL=true means it IS a real line item
                                pass
                            item_name = (item.get('DETAILS') or item.get('PART_NO') or 'Unknown').strip()
                            qty = float(item.get('QTY') or 1)
                            amount = float(item.get('AMOUNT') or 0)
                            cost = float(item.get('COST') or 0) * qty
                            unit_price = amount / qty if qty > 0 else 0
                            
                            c.execute(
                                "INSERT INTO sale_items(sale_id, item_name, category_id, category_code, "
                                "cost_price, sell_price, qty, line_total) "
                                "VALUES(?,?,?,?,?,?,?,?)",
                                (sale_id, item_name, None, 'EZI',
                                 cost, unit_price, int(qty), amount)
                            )
                            results['sale_items'] += 1
            except Exception as e:
                results['errors'].append(f"INVOICE: {e}")
        
        return results
    
    finally:
        # Cleanup temp dir
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def preview_ezi_backup(zip_path):
    """Preview an Ezi POS backup without importing.
    Returns summary stats for the UI.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmpdir)
        
        summary = {
            'company': None,
            'sales': 0,
            'sale_items': 0,
            'stock_items': 0,
            'debtors': 0,
            'creditors': 0,
            'total_revenue': 0,
            'date_range': None,
            'barcodes': 0,
        }
        
        # Company
        company_path = os.path.join(tmpdir, 'COMPANY.DBF')
        if os.path.exists(company_path):
            try:
                table = DBF(company_path, encoding='latin-1')
                if len(table) > 0:
                    rec = table[0]
                    summary['company'] = (rec.get('NAME1', '') or '').strip()
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        # Sales
        invoice_path = os.path.join(tmpdir, 'INVOICE.DBF')
        if os.path.exists(invoice_path):
            try:
                table = DBF(invoice_path, encoding='latin-1')
                summary['sales'] = len(table)
                total = sum(float(r.get('AMOUNT') or 0) for r in table)
                summary['total_revenue'] = round(total, 2)
                dates = [str(r.get('DATE', '')) for r in table if r.get('DATE')]
                if dates:
                    summary['date_range'] = f"{min(dates)} to {max(dates)}"
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        # Sale items
        invtrans_path = os.path.join(tmpdir, 'INVTRANS.DBF')
        if os.path.exists(invtrans_path):
            try:
                table = DBF(invtrans_path, encoding='latin-1')
                summary['sale_items'] = len(table)
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        # Stock
        stock_path = os.path.join(tmpdir, 'STOCK.DBF')
        if os.path.exists(stock_path):
            try:
                table = DBF(stock_path, encoding='latin-1')
                summary['stock_items'] = len(table)
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        # Debtors (customers)
        debtors_path = os.path.join(tmpdir, 'DEBTORS.DBF')
        if os.path.exists(debtors_path):
            try:
                table = DBF(debtors_path, encoding='latin-1')
                summary['debtors'] = len(table)
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        # Creditors (suppliers)
        creditor_path = os.path.join(tmpdir, 'CREDITOR.DBF')
        if os.path.exists(creditor_path):
            try:
                table = DBF(creditor_path, encoding='latin-1')
                summary['creditors'] = len(table)
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        # Barcodes
        barcode_path = os.path.join(tmpdir, 'BARCODE.DBF')
        if os.path.exists(barcode_path):
            try:
                table = DBF(barcode_path, encoding='latin-1')
                summary['barcodes'] = len(table)
            except Exception as _e:
                logger.warning("Silent exception in ezi_import.py: %s", _e, exc_info=True)
        return summary
    
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
