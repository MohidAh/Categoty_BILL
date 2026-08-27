"""BillBook in-app help system — FAQ knowledge base + AI help assistant.

Provides:
1. A local FAQ knowledge base (~60 common questions) with keyword matching
2. /api/help/ask endpoint: tries Groq first, falls back to local FAQ
3. /api/help/articles endpoint: structured guide articles by category
4. Role-based filtering (cashier sees POS-focused; manager sees everything)

The FAQ is the offline-safe fallback. When Groq is available, the AI gets
the FAQ + system documentation as context and can answer novel questions.
"""
import json, re, logging
from .db import conn

logger = logging.getLogger(__name__)


# ─── FAQ KNOWLEDGE BASE ────────────────────────────────────────────────────
# Each entry: {keywords, question, answer, category, roles}
# Roles: ["cashier", "manager", "admin"] — who can see this

FAQ = [
    # ─── POS: Making Sales ───
    {"keywords": ["sale", "sell", "checkout", "cart", "make sale", "new sale"],
     "question": "How do I make a sale?",
     "answer": "1. Go to the POS app (press P or click POS in the sidebar).\n2. Click the category buttons to add items to the cart.\n3. Adjust quantities with the + and - buttons.\n4. Click 'Checkout' (or press F9).\n5. Choose payment method (cash, card, credit, split).\n6. If split, enter cash and card amounts separately.\n7. Click 'Complete Sale' to finish.\nThe receipt will appear — you can print it or send via SMS/WhatsApp.",
     "category": "POS", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["hold", "park", "recall", "held order"],
     "question": "How do I hold and recall an order?",
     "answer": "To hold: While in the cart, press F10 or click 'Hold'. The cart is saved with a reference number.\nTo recall: Click 'Holds' in the POS sidebar, find the order, and click 'Recall'. The cart will be restored.",
     "category": "POS", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["quote", "quotation", "save cart"],
     "question": "How do I create a quotation?",
     "answer": "While in the cart, press F12 or click 'Save as Quote'. The cart is saved as a quotation. To convert it to a sale later, go to 'Quotes' in the POS sidebar and click 'Convert to Sale'.",
     "category": "POS", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["return", "refund", "exchange", "give back"],
     "question": "How do I process a return or refund?",
     "answer": "1. Find the sale in the sales history (POS → Sales History).\n2. Click on the sale to see details.\n3. Click 'Refund' (manager PIN required).\n4. Enter the reason for the refund.\n5. The sale is marked as refunded and stock is returned to inventory.\nNote: Refunds are logged in the Suspicious Activity feed for audit.",
     "category": "POS", "roles": ["manager", "admin"]},

    {"keywords": ["discount", "discount pin", "manager pin", "override"],
     "question": "How do discounts and the manager PIN work?",
     "answer": "Discounts up to 10% (configurable in Settings) can be applied without a PIN.\nFor discounts above the threshold:\n1. Enter the discount amount/percentage in the cart.\n2. Click 'Checkout'.\n3. A PIN modal will appear.\n4. A manager enters their PIN.\n5. The sale completes with the discount applied.\nAll high-discount sales are logged in Suspicious Activity.",
     "category": "POS", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["shift", "close shift", "end shift", "denomination", "variance", "blind"],
     "question": "How do I start and end a shift?",
     "answer": "Start: Go to POS → Shifts → 'Start Shift'. Enter the opening cash amount.\nEnd: Click 'End Shift'. Count the cash in the drawer by denomination (5000, 1000, 500, etc.). The system calculates the variance (counted vs expected).\nBlind close (optional): If enabled in Settings, the expected amount is hidden during counting to prevent rounding bias.",
     "category": "POS", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["bundle", "combo", "3 for"],
     "question": "How do bundles/combos work?",
     "answer": "Bundles group multiple categories into a single product sold at a combined price (e.g., '3-for-Rs1000').\nWhen a bundle is sold, each component category's stock is decremented individually. The bundle price is allocated proportionally to each component so COGS and margin stay correct.\nCreate bundles in Settings → Bundles.",
     "category": "POS", "roles": ["manager", "admin"]},

    {"keywords": ["happy hour", "price rule", "discount time"],
     "question": "How does happy-hour pricing work?",
     "answer": "Happy-hour rules apply a discount percentage during a specific time window (e.g., 10% off 8-10 AM).\nRules can apply to all categories or a specific one. The POS automatically applies the discount during the window and shows an amber 'HH -10%' badge on category tiles.\nConfigure in Settings → Price Rules.",
     "category": "POS", "roles": ["manager", "admin"]},

    {"keywords": ["scan", "barcode", "camera", "F8"],
     "question": "How do I scan barcodes?",
     "answer": "Press F8 in the POS to open the scan modal. If your device has a camera, it will use the browser's BarcodeDetector API to scan. Point the camera at the barcode on the category button.\nIf no camera is available, you can type the category code manually.\nNote: Camera scanning works on Android and laptops with webcams.",
     "category": "POS", "roles": ["cashier", "manager", "admin"]},

    # ─── Billing: Bill Upload & Extraction ───
    {"keywords": ["upload", "bill", "supplier bill", "photo", "pdf", "scan bill"],
     "question": "How do I upload a supplier bill?",
     "answer": "1. Go to the Billing app (press B).\n2. Click 'New Bill'.\n3. Upload photos (JPG/PNG) or PDFs of the supplier bill.\n4. The AI will automatically extract line items, prices, and quantities.\n5. Review the extraction — fix any errors by clicking on the fields.\n6. Click 'Confirm' to add the stock to your inventory.\nThe bill status changes from 'Review' to 'Confirmed'.",
     "category": "Billing", "roles": ["manager", "admin"]},

    {"keywords": ["extraction", "ai", "gemini", "groq", "extract failed"],
     "question": "Why did AI extraction fail or give wrong results?",
     "answer": "Common causes:\n1. No AI provider configured — go to Settings → General → AI Providers and add a Gemini or Groq API key.\n2. Poor image quality — ensure good lighting and the bill fills the frame.\n3. Multi-page bills — upload all pages; the AI processes them together.\n4. Unusual format — the AI may struggle with handwritten or non-standard bills. You can always edit the fields manually.\n5. API rate limit — wait a minute and retry.\nIf extraction consistently fails, check Settings → AI Providers → 'Test Connection'.",
     "category": "Billing", "roles": ["manager", "admin"]},

    {"keywords": ["confirm", "confirm bill", "review bill", "flags"],
     "question": "What do the bill flags mean?",
     "answer": "Flags are warnings that the AI or validation system detected something unusual:\n- 'total mismatch' — the sum of line items doesn't match the written total.\n- 'phone looks odd' — the supplier phone format is invalid.\n- 'low confidence' — the AI isn't sure about a line item.\n- 'price anomaly' — a price is unusually high/low vs historical data.\n- 'needs review' — the AI flagged this line for manual verification.\nReview flagged items carefully before confirming. You can edit any field.",
     "category": "Billing", "roles": ["manager", "admin"]},

    # ─── Inventory ───
    {"keywords": ["stock", "inventory", "levels", "how much stock", "low stock"],
     "question": "How do I check stock levels?",
     "answer": "Go to the Inventory app (press F). The Stock Levels page shows per category: current quantity, stock value, average cost, and status (OK/Low/Out/Negative).\nLow stock = less than 10 pieces. Negative stock means more was sold than purchased — check for unconfirmed bills or run 'Rebuild Stock State'.",
     "category": "Inventory", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["adjust", "adjustment", "damaged", "lost", "damage stock"],
     "question": "How do I adjust stock for damaged or lost items?",
     "answer": "1. Go to Inventory → Adjustments.\n2. Click 'New Adjustment'.\n3. Select the category.\n4. Enter the delta (negative for loss, positive for found stock).\n5. Enter a reason (required, min 3 characters).\n6. Save.\nThe adjustment updates stock immediately and is logged in the Audit Trail. Negative adjustments reduce stock value at the current average cost.",
     "category": "Inventory", "roles": ["manager", "admin"]},

    {"keywords": ["rebuild", "rebuild stock", "recalc", "fix stock"],
     "question": "When should I 'Rebuild Stock State'?",
     "answer": "Click 'Rebuild Stock State' (Inventory → Stock Levels) when:\n1. Stock numbers look wrong or negative.\n2. After importing historical data.\n3. After a database restore.\n4. If the running average cost seems incorrect.\nThe rebuild replays ALL confirmed bills and sales chronologically to recompute the correct running weighted average cost. It's safe to run anytime — it's idempotent.",
     "category": "Inventory", "roles": ["manager", "admin"]},

    # ─── Customers ───
    {"keywords": ["customer", "urdhaar", "credit", "outstanding", "owe"],
     "question": "How do I track customer credit (urdhaar)?",
     "answer": "When a sale is made with payment_method='credit', the sale total is added to the customer's total_credit balance.\nTo view: Customers app → 'Credit Outstanding' tab shows all customers with unpaid credit.\nTo record payment: Click on a customer → 'Record Payment' → enter amount.\nThe daily summary shows total overdue urdhaar.",
     "category": "Customers", "roles": ["manager", "admin"]},

    {"keywords": ["loyalty", "points", "redeem"],
     "question": "How does the loyalty program work?",
     "answer": "Customers earn 1 loyalty point per Rs 10 spent (configurable). Points accumulate automatically on each sale.\nTo redeem: At checkout, enter the number of points to redeem. Each point = Rs 1 discount.\nCustomers are assigned tiers (Bronze/Silver/Gold/Platinum) based on total spent.\nView loyalty history on the Customer Detail page.",
     "category": "Customers", "roles": ["cashier", "manager", "admin"]},

    # ─── Reports & Profit ───
    {"keywords": ["profit", "margin", "earnings", "dashboard", "how much profit"],
     "question": "How do I see my profit?",
     "answer": "Go to the Reports app (press R). The default page is the Store Profit Dashboard, which shows:\n- Actual Overall Gross Margin (the big green number — your primary KPI)\n- Today's sales and gross profit\n- This month's sales, COGS, gross profit, operating profit\n- YTD (year-to-date) cumulative margin\n- Cash buckets (how much you can safely withdraw)\nThe dashboard answers all 9 key business questions on one page.",
     "category": "Reports", "roles": ["manager", "admin"]},

    {"keywords": ["cogs", "cost of goods", "opening closing", "bridge"],
     "question": "What is COGS and how is it calculated?",
     "answer": "COGS = Cost of Goods Sold. BillBook uses the inventory bridge formula:\nCOGS = Opening Inventory + Purchases − Closing Inventory\n\nThis is more accurate than just summing individual sale costs because it accounts for stock shrinkage and adjustments.\n\nThe 'cogs_from_sales' field shows the sum of sale_items.cost_price × qty for cross-checking. They should match within rounding when there are no stock adjustments.",
     "category": "Accounting", "roles": ["manager", "admin"]},

    {"keywords": ["two margins", "category average", "actual overall", "difference margin"],
     "question": "Why are there two margin numbers?",
     "answer": "1. Category Average Margin (informational) — the simple average of each category's margin. Treats all categories equally regardless of how many units sell.\n2. Actual Overall Gross Margin (primary KPI) — Total Gross Profit ÷ Total Sales. This is sales-mix weighted — it reflects what you actually sold.\n\nIf you sell more of the low-margin category, the Actual Overall will be below the Category Average. If you sell more of the high-margin category, it will be above. The difference is the 'sales-mix effect'.",
     "category": "Accounting", "roles": ["manager", "admin"]},

    {"keywords": ["cash drawer", "drawer not profit", "why cash different", "tied stock"],
     "question": "Why doesn't my cash drawer equal my profit?",
     "answer": "Cash drawer ≠ profit because:\n1. Tied in unsold stock — money spent on inventory that hasn't sold yet is in the drawer as goods, not cash.\n2. Owed to you — customers who bought on credit (urdhaar) haven't paid yet.\n3. You owe suppliers — unpaid supplier bills reduce your real cash position.\n4. Owner draws — money you withdrew for personal use reduces cash but isn't an expense.\n\nThe 'Cash Reality' panel on the Actual Earnings page shows all four adjustments.",
     "category": "Accounting", "roles": ["manager", "admin"]},

    {"keywords": ["owner draw", "withdrawal", "personal", "draw"],
     "question": "What is an owner draw and how is it different from an expense?",
     "answer": "An owner draw is money you take out of the business for personal use. It is NOT a business expense — it's a distribution of profit.\nIn BillBook:\n- Operating expenses (rent, salaries, electricity) reduce your net profit.\n- Owner draws reduce your cash but do NOT reduce net profit.\n- Owner draws appear separately in the P&L and Cash Flow.\nUse the 'Withdraw' button on the Cash Buckets page to record a draw.",
     "category": "Accounting", "roles": ["manager", "admin"]},

    {"keywords": ["running average", "weighted average", "cost price", "avg cost"],
     "question": "How does the running weighted average cost work?",
     "answer": "BillBook uses running weighted average costing (not FIFO or simple average).\n\nFormula: New Avg = (Existing Stock Value + New Purchase Value) ÷ (Existing Qty + New Qty)\n\nKey rule: A sale does NOT change the average cost — it reduces quantity and value proportionally. Only purchases shift the average.\n\nExample: Buy 10,000 @ Rs 180 → avg = 180. Sell 3,000 → avg still 180. Buy 10,000 @ Rs 190 → avg = 185.88 (not 185.00).\n\nThis correctly accounts for the fact that sold pieces left the pool before the new purchase arrived.",
     "category": "Accounting", "roles": ["manager", "admin"]},

    {"keywords": ["break even", "break-even", "daily target", "must sell"],
     "question": "What is break-even and how is it calculated?",
     "answer": "Break-even = the sales level at which you cover all fixed costs.\n\nFormula: Break-Even Sales = Fixed Monthly Costs ÷ Actual Overall Margin\n\nFixed costs = sum of recurring expenses marked as 'is_fixed' (rent, salaries).\nThe dashboard shows 'Must sell Rs X/day' — divide break-even by 30 days.\n'Y/day so far' shows today's actual sales.",
     "category": "Accounting", "roles": ["manager", "admin"]},

    # ─── Expenses ───
    {"keywords": ["expense", "add expense", "recurring", "budget"],
     "question": "How do I add and manage expenses?",
     "answer": "Go to Reports → Expenses.\nTo add: Click 'Add Expense', select category, enter amount, choose type (Operating or Owner Draw), select date.\nRecurring: Click 'Recurring' to set up auto-generated expenses (e.g., rent on the 1st of each month). The system generates them automatically on startup.\nBudgets: Set a monthly budget per category. The budget card shows actual vs budget with a progress bar (green ≤80%, amber 80-100%, red >100%).",
     "category": "Expenses", "roles": ["manager", "admin"]},

    # ─── Settings ───
    {"keywords": ["setting", "config", "change password", "employee", "staff", "pin"],
     "question": "How do I manage staff and PINs?",
     "answer": "Go to Settings → Employees.\n- Add employee: name, role (cashier/manager/admin), 4-8 digit PIN.\n- Cashiers can only use POS; managers can access everything except settings.\n- Activate/deactivate employees as needed.\n- To change a PIN, edit the employee and enter a new one.\nStaff log in at the POS with their PIN.",
     "category": "Settings", "roles": ["manager", "admin"]},

    {"keywords": ["backup", "restore", "data loss", "export data"],
     "question": "How do I back up my data?",
     "answer": "In-app: Settings → Backups → 'Backup Now'. Creates a timestamped copy in data/backups/.\nScheduled: Run backup.bat via Windows Task Scheduler nightly.\nCloud sync: Point Google Drive / OneDrive at the data/backups/ folder for offsite copies.\nTo restore: Stop the server, copy the backup file to data/billbook.db, restart.",
     "category": "Settings", "roles": ["manager", "admin"]},

    {"keywords": ["ai provider", "gemini key", "groq key", "api key", "test connection"],
     "question": "How do I set up AI providers?",
     "answer": "1. Get a free Gemini API key from https://aistudio.google.com/apikey\n2. Get a free Groq API key from https://console.groq.com/keys\n3. Go to Settings → General → AI Providers.\n4. Click 'Add Provider', select type (Gemini/Groq), paste the key.\n5. Click 'Test Connection' to verify.\n6. Set priority (lower = used first).\n\nGemini = bill image extraction (vision).\nGroq = AI chat assistant (text).\nAPI keys are encrypted at rest with Fernet.",
     "category": "Settings", "roles": ["manager", "admin"]},

    # ─── Mobile & Connectivity ───
    {"keywords": ["phone", "mobile", "android", "iphone", "pair", "connect phone", "lan"],
     "question": "How do I connect my phone to BillBook?",
     "answer": "1. On the shop PC, go to Settings → General → enable 'LAN Mode'.\n2. Note the IP address shown (e.g., 192.168.1.100:8000).\n3. On your phone, open BillBook (Android app or browser).\n4. Enter the IP address.\n5. On the shop PC, go to Settings → Devices → 'Generate Pairing Code'.\n6. Choose role (cashier or manager).\n7. Enter the 6-digit code on your phone.\n8. Your phone now has full access with the assigned role.",
     "category": "Connectivity", "roles": ["manager", "admin"]},

    {"keywords": ["remote", "tunnel", "cloudflare", "away from shop", "home"],
     "question": "How do I access BillBook from home?",
     "answer": "Use Cloudflare Tunnel (free, no port-forwarding):\n1. On the shop PC, run scripts/remote-access.bat.\n2. It prints a public URL (e.g., https://billbook-abc.trycloudflare.com).\n3. Open this URL on your phone browser from anywhere.\n4. Log in with your password.\n5. Pair your phone as a manager.\nNote: Quick tunnel URLs are temporary — they change on restart. For a permanent URL, use a named tunnel (see INSTALL_GUIDE.md).",
     "category": "Connectivity", "roles": ["manager", "admin"]},

    {"keywords": ["offline", "no internet", "offline sale", "sync", "queue"],
     "question": "Can I make sales when the internet is down?",
     "answer": "Yes. The POS works fully offline:\n1. Sales are stored locally in the browser (IndexedDB).\n2. A badge shows the queue count.\n3. When the connection returns, sales auto-sync to the server.\n4. If sync fails 5 times, the sale stays queued — you can retry manually.\n\nNote: The POS, profit engine, and inventory NEVER depend on an LLM or internet. Only AI features (extraction, chat) need internet.",
     "category": "Troubleshooting", "roles": ["cashier", "manager", "admin"]},

    # ─── Troubleshooting ───
    {"keywords": ["won't load", "blank page", "not working", "white screen", "console error"],
     "question": "The app shows a blank page — what do I do?",
     "answer": "1. Hard refresh: Ctrl+Shift+R (or Cmd+Shift+R on Mac).\n2. Clear cookies for localhost:8000.\n3. Check if the server is running (look for the command prompt window).\n4. If the server crashed, restart it (double-click start.bat).\n5. Check data/app.log for error messages.\n6. If still blank, try a different browser (Chrome recommended).",
     "category": "Troubleshooting", "roles": ["cashier", "manager", "admin"]},

    {"keywords": ["login", "can't login", "login loop", "password", "forgot"],
     "question": "I can't log in — what do I do?",
     "answer": "1. Make sure you're using the manager password (not a staff PIN).\n2. Check Caps Lock.\n3. If you forgot the password, you can reset it:\n   - Stop the server.\n   - Edit the .env file: set APP_PASSWORD=newpassword\n   - Delete data/billbook.db (WARNING: loses all data — backup first!).\n   - Or: open data/billbook.db with a SQLite tool and clear the 'password_hash' row in 'settings'.\n   - Restart the server — it will re-seed the password from .env.\n4. If stuck in a login loop, clear cookies and restart.",
     "category": "Troubleshooting", "roles": ["manager", "admin"]},

    {"keywords": ["port", "8000 in use", "port conflict", "address in use"],
     "question": "It says 'Port 8000 is in use' — what do I do?",
     "answer": "Another program is using port 8000. Options:\n1. Close the other program (common: another instance of BillBook, Skype, or a dev server).\n2. Change the port: edit start.bat, change --port 8000 to --port 8080.\n3. Access the app at http://localhost:8080 instead.\n4. To find what's using port 8000: open Command Prompt, run 'netstat -ano | findstr :8000', then 'taskkill /PID <number> /F'.",
     "category": "Troubleshooting", "roles": ["manager", "admin"]},

    {"keywords": ["database locked", "db locked", "sqlite locked"],
     "question": "It says 'Database is locked' — what do I do?",
     "answer": "This means another process is using the database. Fix:\n1. Stop all running BillBook instances (check Task Manager for python.exe).\n2. Delete data/billbook.db-wal and data/billbook.db-shm (these are temporary files).\n3. Restart the server.\n4. If it persists, restore from a backup.\nThis is rare with WAL mode but can happen if the server crashes mid-write.",
     "category": "Troubleshooting", "roles": ["manager", "admin"]},

    {"keywords": ["negative stock", "oversold", "stock negative", "more sold than purchased"],
     "question": "Why is my stock negative?",
     "answer": "Negative stock means more items were sold than purchased for that category. Causes:\n1. An unconfirmed bill — the stock was sold before the purchase was recorded.\n2. A data import that didn't include all historical bills.\n3. Manual database edits.\n\nFix: Go to Inventory → Stock Levels → 'Rebuild Stock State'. This replays all bills and sales chronologically and corrects the running average.\n\nIf the negative persists, check that all supplier bills are confirmed (Billing app → Review Queue).",
     "category": "Troubleshooting", "roles": ["manager", "admin"]},

    # ─── AI Features ───
    {"keywords": ["ai assistant", "chat", "ask ai", "insights", "agent"],
     "question": "What can the AI assistant do?",
     "answer": "The AI assistant (AI Insights app) can:\n1. Answer business questions: 'What's my margin?', 'How much cash can I withdraw?', 'What's my break-even?'\n2. Run multi-step analysis: 'Compare this month vs last month'\n3. Draft actions: 'Draft a PO for the lowest category' → creates a pending action for your approval\n4. Prepare for seasons: 'Prepare for Eid' → drafts POs, happy-hour rules, and customer broadcasts\n5. Answer accounting questions: 'What is COGS?', 'Why doesn't drawer cash equal profit?'\n\nThe AI NEVER executes actions without your approval. It prepares; you decide.",
     "category": "AI Features", "roles": ["manager", "admin"]},

    {"keywords": ["approval queue", "pending action", "approve", "ai draft"],
     "question": "What is the Approval Queue?",
     "answer": "The Approval Queue is where AI-drafted actions wait for your approval before executing.\nWhen the AI suggests an action (e.g., 'apply new price', 'draft a PO', 'confirm a bill'), it creates a pending action card showing:\n- What: the action type\n- Why: the AI's reasoning\n- Impact: estimated effect\n\nYou can: Approve (executes the action), Edit (modify then approve), or Reject (discards).\nAll approvals are logged in the Audit Trail with created_by='ai'.\nPrice changes require a manager PIN.",
     "category": "AI Features", "roles": ["manager", "admin"]},

    {"keywords": ["kill switch", "disable ai", "turn off ai", "ai off"],
     "question": "How do I turn off AI features?",
     "answer": "Go to Settings → AI → toggle the 'AI Kill Switch'.\nWhen ON (disabled):\n- All AI tasks stop immediately.\n- The POS, profit engine, and inventory continue normally.\n- Heuristic/local features (trends, break-even, margin alerts) still work.\n- The AI assistant shows 'AI is disabled'.\n- Pending actions remain in the queue for manual review.\nToggle it back OFF to re-enable AI.",
     "category": "AI Features", "roles": ["manager", "admin"]},

    # ─── v8.1 New Features ───
    {"keywords": ["wizard", "setup", "first", "launch", "install", "password"],
     "question": "How does the first-launch wizard work?",
     "answer": "On a fresh install, BillBook shows a 4-step wizard instead of the raw login page:\n1. Set Password (with strength meter, min 8 chars)\n2. Pick Business Type (Wholesale/Retail/Custom) — pre-fills categories\n3. Confirm Categories (edit names/prices, add/remove)\n4. Optional Gemini key + choose start page (Launcher/Dashboard/POS)\nAfter finishing, you're logged in and land on your chosen start page. The wizard never appears again (it's skipped if setup_completed is already true).",
     "category": "v8.1 Features", "roles": ["manager", "admin"]},
    {"keywords": ["qr", "pair", "scan", "device", "branch", "register"],
     "question": "How do I pair a device or register a branch via QR?",
     "answer": "Device pairing: Go to Settings → Devices → 'Show QR'. A QR code encodes the pairing code + server URL + role. Scan it with your phone's camera → auto-pairs (no manual IP or code entry).\nBranch registration: Go to AI Insights → HQ Branches → 'Add Branch via QR'. The QR encodes the HQ URL + registration code. The branch scans it → auto-registers.\nThe existing 6-digit code flow still works as a fallback.",
     "category": "v8.1 Features", "roles": ["manager", "admin"]},
    {"keywords": ["remote", "tunnel", "cloudflare", "access", "internet"],
     "question": "How do I enable remote access?",
     "answer": "Go to Settings → Remote Access → toggle 'Enable'. BillBook spawns a Cloudflare quick tunnel and shows the HTTPS URL (e.g., https://billbook-abc123.trycloudflare.com). Copy it and open on your phone. Toggle 'Disable' to stop. The state persists across restarts.\nNote: Quick tunnel URLs change on restart. For a permanent URL, connect a free Cloudflare account and set up a named tunnel.",
     "category": "v8.1 Features", "roles": ["manager", "admin"]},
    {"keywords": ["backup", "auto", "maintain", "diagnose", "health"],
     "question": "How do automatic backups and diagnostics work?",
     "answer": "Auto-backup (Zero-Config): a timestamped backup is created daily into data/backups/, retaining the last 10. No action needed.\nDiagnose (One-Click): go to Help → 'Diagnose' button. Runs 6 health checks: DB integrity, free disk space, AI provider status, tunnel status, last-backup age, and negative-stock categories. Each shows green/amber/red.\nUpdate check: on startup, checks GitHub Releases for a newer version and shows a banner if available.",
     "category": "v8.1 Features", "roles": ["manager", "admin"]},
    {"keywords": ["drag", "drop", "upload", "bill", "pdf", "image"],
     "question": "Can I drag and drop a bill to upload it?",
     "answer": "Yes — v8.1 adds a global drag-drop handler. Drop a PDF or image file on ANY page in BillBook. A 'Drop to upload bill' overlay appears, and you're automatically navigated to the New Bill page where extraction starts. No need to navigate to Bills → New first.",
     "category": "v8.1 Features", "roles": ["manager", "admin"]},
    {"keywords": ["profit", "ticker", "today", "topbar", "expense", "fab"],
     "question": "What are the profit ticker and quick expense FAB?",
     "answer": "Profit ticker: a green chip in the topbar showing today's gross profit. Updates after each sale. Click it to open the Store Profit Dashboard.\nQuick Expense FAB: a red floating '+' button at the bottom-right (above the Help '?'). Click it → a 2-field modal appears (amount + category) → save instantly. No need to navigate to the Expenses page for a quick entry.",
     "category": "v8.1 Features", "roles": ["manager", "admin"]},

    # ─── v8.2 New Features ───
    {"keywords": ["audit", "auditor", "check", "integrity", "earnings"],
     "question": "What does the AI Auditor do?",
     "answer": "The AI Auditor runs 8 checks across 5 domains (integrity, financial, fraud, operational, compliance) to verify your business health:\n- Earnings formula: Sales - COGS - OpEx = earnings\n- Over-withdrawal: flags if you've withdrawn more than the safe limit\n- Stock reserve: days of cover vs target\n- Negative stock, refund anomalies, unconfirmed bills\nAll checks are deterministic math on local data — fully offline, no LLM required.\nGo to Reports → AI Auditor → 'Run Audit' to check on demand.",
     "category": "v8.2 Features", "roles": ["manager", "admin"]},
    {"keywords": ["safe", "withdrawal", "over", "limit", "cash"],
     "question": "How does safe withdrawal work?",
     "answer": "Safe withdrawal = Cash - Stock Replacement - Operating Expenses - Business Reserve.\nThe auditor computes this automatically. If you withdraw more than the safe limit, it flags a CRITICAL finding with the exact over-amount. The Cash Buckets page shows a green 'Safe to withdraw Rs X' or red 'Over-withdrawn by Rs Y' banner. You're never hard-blocked — a manager PIN lets you proceed, but the over-withdrawal is logged for the next audit.",
     "category": "v8.2 Features", "roles": ["manager", "admin"]},
    {"keywords": ["sell", "through", "bill", "intelligence", "overstock"],
     "question": "What is Bill Intelligence (sell-through check)?",
     "answer": "When you confirm a new bill, BillBook checks each category against the previous purchase:\n- Sold >= 80% → 'Well-timed' (green)\n- 40-80% sold → 'Partially sold' (info)\n- < 40% sold → 'Overstock risk' (red, soft pause)\nThe soft pause asks: Cancel / Confirm Anyway / It's intentional (seasonal/discount/new stock). Choosing to proceed logs it so the auditor won't re-flag it. First-ever purchases skip the check.",
     "category": "v8.2 Features", "roles": ["manager", "admin"]},

    # ─── v8.12 New Features ───
    {"keywords": ["pos", "dark", "theme", "professional", "tier", "qty", "multiplier", "hotkey"],
     "question": "What's new in the POS UI (v8.12)?",
     "answer": "v8.12 redesigns the POS screen with a dark navy theme for high contrast at the cashier counter, tiered category cards with colored borders (emerald/sky/amber/violet/pink/teal/rose), a hotkey number badge on each tile (1–7), a QTY multiplier row at the top of the items panel (×1 / ×2 / ×3 / ×5 / ×10 — tap once to set, then every category you tap adds that quantity), and a refined Sale Complete modal with a big green checkmark, prominent total, and teal CTAs (Receipt + New Sale). Number keys 1–5 select the QTY multiplier; F1–F7 still add categories.",
     "category": "v8.12 Features", "roles": ["cashier", "manager", "admin"]},
    {"keywords": ["discount", "line", "per-item", "override", "price", "pin"],
     "question": "How do per-item discounts work?",
     "answer": "On any cart line in the POS, click the pencil (edit) icon to open the per-item discount popover. You can pick a quick % (0/5/10/15/20/25/50), enter a custom %, enter a fixed Rs amount, OR override the unit price entirely. Price overrides require a manager PIN. The cart shows the strikethrough original price + the discounted price + a small chip showing the discount amount. Order-level discounts (top of the right panel) stack on top of per-item discounts.",
     "category": "v8.12 Features", "roles": ["cashier", "manager", "admin"]},
    {"keywords": ["easypaisa", "jazzcash", "raast", "online", "payment", "sub-method", "submethod"],
     "question": "What are payment sub-methods?",
     "answer": "When a customer pays via Online, you can also record the specific sub-method: Easypaisa, JazzCash, Raast QR, or Bank Transfer. The sub-method is stored on the sale row (sales.payment_submethod) so you can filter reports by digital wallet provider. This is purely informational — the cash_drawer is not affected by online payments.",
     "category": "v8.12 Features", "roles": ["cashier", "manager", "admin"]},
    {"keywords": ["delete", "supplier", "customer", "soft", "void", "refund", "kpi", "tile", "count"],
     "question": "Why did my supplier/customer count go down after I deleted one?",
     "answer": "v8.11+ uses soft-delete for suppliers and customers (deleted_at timestamp). They disappear from lists, KPI tiles, 'Top suppliers by spend', customer counts, and the AR/AP aging reports, but their historical bills/sales remain intact for audit. If you still see a deleted entity in a count or tile, run Settings → Data Reconciliation → Repair (admin only). Sales have a separate 'voided' status for admin-corrected mistakes — voided sales also disappear from all reports but stay in the audit log.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["pos", "import", "sync", "ezi", "backup", "deleted", "modified", "dry-run"],
     "question": "How does POS Import Sync detect deleted + modified sales?",
     "answer": "When you import an Ezi POS backup zip, BillBook first runs a dry-run analysis that compares the backup's sale records to what's already in BillBook's database. (1) New sales (in backup, not in BillBook) → imported. (2) Deleted sales (in BillBook, not in backup) → soft-deleted in BillBook to keep the two systems in sync. A configurable 5% threshold protects you from accidentally voiding dozens of sales if your Ezi POS data got corrupted. (3) Modified sales (line items changed) → re-imported; old sale soft-deleted, new sale inserted. Detection is via UNQCODE + SHA-256 line-item checksum.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["reconcile", "repair", "discrepancy", "integrity", "stock_state"],
     "question": "What is the Data Reconciliation tool?",
     "answer": "Settings → Data Reconciliation (admin only) runs a full integrity scan: it checks that sales totals match the sum of sale_items, that cash_drawer entries match the sum of cash sales, and that stock_state matches the replayed history of purchases - sales + adjustments. If any mismatches are found, the Repair tool fixes them in a single atomic transaction. Safe to run any time — it's idempotent.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["atomic", "transaction", "rollback", "crash", "safe"],
     "question": "What does 'atomic transaction' mean in BillBook?",
     "answer": "BillBook v8.6+ wraps every multi-step mutation (create_sale, refund_sale, confirm_bill, POS import per-sale) in a single SQLite BEGIN IMMEDIATE transaction. This means all side effects commit together OR all roll back together. If the power goes out mid-refund, you'll never end up with a sale marked refunded but the stock not reversed, or commission not reversed but the sale marked refunded. On restart, the database is always in a consistent state.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["capital", "injection", "investment", "negative", "withdrawal", "owner", "pocket", "opening", "balance", "day 1"],
     "question": "Why is my 'Available for Withdrawal' negative, and how do I fix it?",
     "answer": "This is the 'Day-1 trap'. When you uploaded your existing supplier bills to BillBook, each confirmed bill wrote a -amount row to cash_drawer (cash went OUT to buy stock). But you never recorded the matching +amount for the initial capital you invested BEFORE BillBook existed. So cash_drawer sum looks negative, even though you have cash. FIX: Go to Billing → Cash Buckets → click the 'Capital Injection' button (next to Withdraw). Enter the capital you originally invested (best estimate is fine), pick a source (Owner's Pocket / Partner / Bank Loan / Opening Balance), enter your admin PIN, and click Record Injection. The injection credits cash_drawer by +amount (so the withdrawal number becomes positive) but does NOT count as revenue — your profit stays accurate.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["capital", "investment", "money", "personal", "inject", "owner_invest"],
     "question": "How do I record money I personally put into the business?",
     "answer": "Go to Billing → Cash Buckets → click 'Capital Injection'. Enter the amount, pick a source (Owner's Pocket for personal savings, Partner for co-owner contributions, Bank Loan for borrowed capital, Opening Balance for a one-time Day-1 fix), choose Cash or Bank Transfer, optionally add a note (e.g. 'Top-up for Diwali stock'), and enter your admin PIN. The injection: (1) credits cash_drawer by +amount so your withdrawal number increases, (2) creates a capital_injections row for audit, (3) logs an activity_log entry. Capital is equity, NOT revenue — it does NOT inflate your sales, COGS, or gross profit numbers.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["supplier", "comparison", "cheapest", "cheaper", "best price", "category", "cost"],
     "question": "How do I find the cheapest supplier for each category?",
     "answer": "Go to Reports → Supplier Comparison (v8.13). For each category, the page lists every supplier who has sold you that category, with their avg/last/min price + delta vs your running avg_cost. The cheapest supplier per category is flagged with a green badge. Use this to negotiate with your current supplier ('XYZ sold me the same category at Rs 75, you're charging Rs 90') or switch to a cheaper one.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["cost", "trend", "margin", "erosion", "increase", "alert", "category", "expensive"],
     "question": "How do I know if a supplier has quietly raised their prices?",
     "answer": "Go to Reports → Category Cost Trends (v8.13). This page tracks each category's running avg_cost over the last 30 days and flags categories whose cost has risen >5% without a corresponding sell-price increase. For each alert, it shows the old cost vs new cost, the % increase, and the margin impact (e.g. 'Category A avg_cost up 12% — margin dropped from 70% to 67%'). This is the #1 silent profit killer in category-based wholesale — costs creep up, sell prices stay, margins quietly die.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},
    {"keywords": ["damage", "expired", "expiry", "theft", "write-off", "writeoff", "shrinkage", "loss", "sample", "display"],
     "question": "How do I record damaged / expired / stolen stock?",
     "answer": "Go to Inventory → Stock Overview → click 'Write Off' (v8.13). Pick the category, enter the qty, choose a reason (damage / expiry / theft / sample / display / other), optionally add a note (e.g. '5 units crushed in transit'), and enter your admin PIN. The write-off: (1) reduces category_stock_state (current_qty -= qty), (2) records the loss value (qty × avg_cost at time of write-off) in a dedicated stock_writeoffs table, (3) writes a stock_adjustments row (delta = -qty) so the existing ledger stays intact, (4) logs an activity_log entry. The monthly P&L shows a separate 'Shrinkage' line item so you can see the true cost of damage per month.",
     "category": "v8.12 Features", "roles": ["manager", "admin"]},

    # ─── Keyboard Shortcuts ───
    {"keywords": ["shortcut", "keyboard", "hotkey", "f1", "f9", "ctrl k"],
     "question": "What are the keyboard shortcuts?",
     "answer": "Navigation: D=Dashboard, P=POS, B=Bills, F=Items, S=Suppliers, R=Reports, I=AI Insights, C=Customers, ,=Settings, N=New Bill, H=Launcher\nPOS: F1-F7=Add categories, F8=Scan, F9=Checkout, F10=Hold, F12=Save Quote\nGlobal: Ctrl+K=Command Palette\nReports: 1-9, 0, E, A, T, M, P, Y, D, B=Report sub-pages",
     "category": "Shortcuts", "roles": ["cashier", "manager", "admin"]},
]


# ─── ARTICLE CATEGORIES (for the Help page) ────────────────────────────────

CATEGORIES = [
    {"id": "POS", "name": "POS & Sales", "icon": "cart", "roles": ["cashier", "manager", "admin"]},
    {"id": "Billing", "name": "Billing & Suppliers", "icon": "bills", "roles": ["manager", "admin"]},
    {"id": "Inventory", "name": "Inventory & Stock", "icon": "box", "roles": ["manager", "admin"]},
    {"id": "Customers", "name": "Customers & Loyalty", "icon": "users", "roles": ["manager", "admin"]},
    {"id": "Reports", "name": "Reports & Profit", "icon": "chart", "roles": ["manager", "admin"]},
    {"id": "v8.1 Features", "name": "v8.1 New Features", "icon": "sparkles", "roles": ["manager", "admin"]},
    {"id": "v8.2 Features", "name": "v8.2 Auditor & Bill Intel", "icon": "shield", "roles": ["manager", "admin"]},
    {"id": "v8.12 Features", "name": "v8.12 POS Refresh + Soft-Delete", "icon": "sparkles", "roles": ["cashier", "manager", "admin"]},
    {"id": "Accounting", "name": "Accounting Explained", "icon": "scale", "roles": ["manager", "admin"]},
    {"id": "Expenses", "name": "Expenses & Budgets", "icon": "wallet", "roles": ["manager", "admin"]},
    {"id": "Settings", "name": "Settings & Staff", "icon": "gear", "roles": ["manager", "admin"]},
    {"id": "Connectivity", "name": "Mobile & Remote", "icon": "phone", "roles": ["manager", "admin"]},
    {"id": "AI Features", "name": "AI Assistant & Automation", "icon": "sparkles", "roles": ["manager", "admin"]},
    {"id": "Troubleshooting", "name": "Troubleshooting", "icon": "alert", "roles": ["cashier", "manager", "admin"]},
    {"id": "Shortcuts", "name": "Keyboard Shortcuts", "icon": "keyboard", "roles": ["cashier", "manager", "admin"]},
]


def get_articles(role: str = "manager") -> list:
    """Return FAQ articles filtered by role, grouped by category."""
    filtered = [f for f in FAQ if role in f.get("roles", ["manager"])]
    return filtered


def get_categories(role: str = "manager") -> list:
    """Return categories filtered by role."""
    return [c for c in CATEGORIES if role in c.get("roles", ["manager"])]


def search_faq(query: str, role: str = "manager") -> list:
    """Search the FAQ by keyword matching. Returns ranked results."""
    query_lower = query.lower()
    query_words = set(re.findall(r'\w+', query_lower))
    results = []
    for entry in FAQ:
        if role not in entry.get("roles", ["manager"]):
            continue
        # Score: count of matching keywords
        score = 0
        for kw in entry["keywords"]:
            kw_lower = kw.lower()
            if kw_lower in query_lower:
                score += 3  # exact keyword phrase match
            else:
                kw_words = set(re.findall(r'\w+', kw_lower))
                overlap = query_words & kw_words
                score += len(overlap)
        # Also check question text
        if query_lower in entry["question"].lower():
            score += 5
        if score > 0:
            results.append({**entry, "score": score})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def answer_help_question(question: str, role: str = "manager") -> dict:
    """Answer a help question using local FAQ first, then Groq if available.

    Returns {answer, source: 'faq'|'ai'|'none', cached: bool, suggestions: list}
    """
    # Step 1: Try local FAQ
    matches = search_faq(question, role)
    if matches and matches[0]["score"] >= 3:
        best = matches[0]
        other_matches = [m["question"] for m in matches[1:4] if m["score"] >= 2]
        return {
            "answer": best["answer"],
            "source": "faq",
            "matched_question": best["question"],
            "cached": False,
            "suggestions": other_matches,
        }

    # Step 2: If no good FAQ match, try Groq via ai_router
    from .ai_router import ai_call, is_ai_disabled
    if is_ai_disabled():
        # Return best available FAQ match even if score is low
        if matches:
            return {
                "answer": matches[0]["answer"],
                "source": "faq_fuzzy",
                "matched_question": matches[0]["question"],
                "cached": False,
                "suggestions": [m["question"] for m in matches[1:4]],
            }
        return {
            "answer": "I couldn't find an answer to that question. Try rephrasing or browse the Help page for articles. AI is currently disabled — enable it in Settings for better answers.",
            "source": "none",
            "cached": False,
            "suggestions": ["How do I make a sale?", "What is my profit?", "How do I pair my phone?"],
        }

    # Build context from FAQ for the AI
    faq_context = "\n\n".join([
        f"Q: {f['question']}\nA: {f['answer']}"
        for f in FAQ if role in f.get("roles", ["manager"])
    ])

    def groq_call():
        from . import extract
        import httpx
        # Get Groq API key
        with conn() as c:
            from .crypto import decrypt_api_key
            row = c.execute(
                "SELECT api_key FROM ai_providers WHERE provider_type='groq' AND enabled=1 ORDER BY priority LIMIT 1"
            ).fetchone()
        if not row:
            raise ValueError("No Groq provider configured")
        api_key = decrypt_api_key(row["api_key"])
        system_prompt = (
            "You are BillBook's built-in help assistant. Answer the user's question about how to use "
            "the BillBook POS system. Be concise, practical, and specific. If the question is about "
            "business data (profit, stock, etc.), tell them which page to check.\n\n"
            "Here is the FAQ knowledge base for reference:\n" + faq_context
        )
        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                "max_tokens": 500,
                "temperature": 0.3,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "response": data["choices"][0]["message"]["content"],
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
            "tokens_in": data.get("usage", {}).get("prompt_tokens", 0),
            "tokens_out": data.get("usage", {}).get("completion_tokens", 0),
        }

    result = ai_call("help_assistant", {"question": question, "role": role},
                     provider_hint="groq", ttl_key="bi", execute_fn=groq_call)

    if result.get("response"):
        suggestions = [m["question"] for m in matches[:3]] if matches else [
            "How do I make a sale?", "What is my profit?", "How do I pair my phone?"
        ]
        return {
            "answer": result["response"],
            "source": "ai",
            "cached": result.get("cached", False),
            "stale": result.get("stale", False),
            "suggestions": suggestions,
        }

    # Step 3: Fallback to best FAQ match even if score is low
    if matches:
        return {
            "answer": matches[0]["answer"],
            "source": "faq_fuzzy",
            "matched_question": matches[0]["question"],
            "cached": False,
            "suggestions": [m["question"] for m in matches[1:4]],
        }

    return {
        "answer": "I couldn't find an answer to that question. Try browsing the Help page or asking differently.",
        "source": "none",
        "cached": False,
        "suggestions": ["How do I make a sale?", "What is my profit?", "How do I pair my phone?"],
    }
