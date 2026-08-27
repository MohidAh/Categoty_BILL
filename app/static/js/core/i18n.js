// i18n v3.1 — Urdu/English label dictionary for POS-facing strings
// Usage: import { t, setLang, getLang } from '../core/i18n.js';
// setLang('ur'); t('new_sale') → "نیا سیل"

const LANG_KEY = 'bb-language';
let currentLang = localStorage.getItem(LANG_KEY) || 'en';

const DICT = {
  en: {
    // POS
    new_sale: 'New Sale',
    current_sale: 'Current Sale',
    customer: 'Customer',
    customer_name: 'Customer name (optional)',
    phone: 'Phone',
    sale_notes: 'Sale notes (optional)',
    cart_empty: 'Cart is empty',
    cart_empty_hint: 'Tap a category button to add items',
    checkout: 'Checkout',
    total: 'Total',
    subtotal: 'Subtotal',
    discount: 'Discount',
    tax: 'Tax',
    payment_method: 'Payment Method',
    cash: 'Cash',
    card: 'Card',
    online: 'Online',
    credit: 'Credit (Urdhaar)',
    split: 'Split Payment',
    pay_now: 'Pay Now',
    sale_complete: 'Sale Complete',
    sale_queued: 'Sale Queued (Offline)',
    print: 'Print',
    whatsapp: 'WhatsApp',
    hold: 'Hold',
    recall: 'Recall',
    clear: 'Clear',
    quote: 'Quote',
    scan: 'Scan',
    history: 'History',
    z_report: 'Z-Report',
    cash_drawer: 'Cash Drawer',
    cash_in: 'Cash In',
    cash_out: 'Cash Out',
    express_mode: 'Express Mode',
    // Stock
    in_stock: 'In Stock',
    low_stock: 'Low Stock',
    out_of_stock: 'Out of Stock',
    // General
    save: 'Save',
    cancel: 'Cancel',
    delete: 'Delete',
    edit: 'Edit',
    add: 'Add',
    close: 'Close',
    search: 'Search',
    loading: 'Loading...',
    error: 'Error',
    success: 'Success',
    retry: 'Retry',
    settings: 'Settings',
    backup: 'Backup',
    export: 'Export',
  },
  ur: {
    // POS
    new_sale: 'نیا سیل',
    current_sale: 'موجودہ سیل',
    customer: 'گاہک',
    customer_name: 'گاہک کا نام (اختیاری)',
    phone: 'فون',
    sale_notes: 'سیل نوٹس (اختیاری)',
    cart_empty: 'کارٹ خالی ہے',
    cart_empty_hint: 'آئٹم شامل کرنے کے لیے کیٹگری بٹن دبائیں',
    checkout: 'چیک آؤٹ',
    total: 'کل',
    subtotal: 'سب ٹوٹل',
    discount: 'رعایت',
    tax: 'ٹیکس',
    payment_method: 'ادائیگی کا طریقہ',
    cash: 'نقد',
    card: 'کارڈ',
    online: 'آن لائن',
    credit: 'ادھار',
    split: 'مشترکہ ادائیگی',
    pay_now: 'ادائیگی کریں',
    sale_complete: 'سیل مکمل',
    sale_queued: 'سیل محفوظ ہوگیا (آف لائن)',
    print: 'پرنٹ',
    whatsapp: 'واٹس ایپ',
    hold: 'روکو',
    recall: 'واپس لاؤ',
    clear: 'صاف کرو',
    quote: 'قیمت',
    scan: 'اسکین',
    history: 'تاریخ',
    z_report: 'زیڈ رپورٹ',
    cash_drawer: 'کیش دراز',
    cash_in: 'کیش ان',
    cash_out: 'کیش آؤٹ',
    express_mode: 'فاسٹ موڈ',
    // Stock
    in_stock: 'اسٹاک میں',
    low_stock: 'اسٹاک کم',
    out_of_stock: 'اسٹاک ختم',
    // General
    save: 'محفوظ کرو',
    cancel: 'منسوخ',
    delete: 'مٹاؤ',
    edit: 'ترمیم',
    add: 'شامل کرو',
    close: 'بند کرو',
    search: 'تلاش',
    loading: 'لوڈ ہو رہا ہے...',
    error: 'خرابی',
    success: 'کامیاب',
    retry: 'دوبارہ کوشش',
    settings: 'ترتیبات',
    backup: 'بیک اپ',
    export: 'برآمد',
  },
};

export function t(key) {
  const dict = DICT[currentLang] || DICT.en;
  return dict[key] || DICT.en[key] || key;
}

export function setLang(lang) {
  if (lang === 'ur' || lang === 'en') {
    currentLang = lang;
    localStorage.setItem(LANG_KEY, lang);
    document.documentElement.setAttribute('data-lang', lang);
    // Dispatch event so components can re-render
    window.dispatchEvent(new CustomEvent('language-changed', { detail: { lang } }));
  }
}

export function getLang() {
  return currentLang;
}

export function toggleLang() {
  setLang(currentLang === 'en' ? 'ur' : 'en');
}
