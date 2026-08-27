"""v8.3 — Tests for sidebar/active-nav fix and expenses/cash-buckets relocation.

Issues fixed:
1. Sidebar first item (appRoute) was incorrectly highlighted as active when
   user navigated to a sub-page (e.g. on /customers/credit, the parent
   "All Customers" was active). The router.js override was using prefix-match
   without excluding the appRoute.
2. Expenses and Cash Buckets were in the Reports app, but they are
   money-actions, not reports. Moved to the Billing app.
   Old URLs /reports/expenses and /reports/cash-buckets redirect to
   /bills/expenses and /bills/cash-buckets respectively.
3. Duplicate "AI Auditor" nav item (was a duplicate of "Audit Trail" with
   same route /reports/audit) — consolidated to one item labeled
   "AI Auditor" (matching the page title).
"""
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_router_isNavActive_excludes_appRoute():
    """The router.js override must exclude appRoute from prefix-match."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "router.js").read_text()
    # The new isNavActive helper must exist and exclude appRoute from prefix-match
    assert "isNavActive" in js, "router.js must define isNavActive helper"
    assert "appRoute" in js, "isNavActive must consider appRoute context"
    assert "item.route !== appRoute" in js or "itemRoute === appRoute" in js, \
        "isNavActive must exclude appRoute from prefix-match"


def _extract_app_section(shell_text, app_key):
    """Extract a single app's config block from shell.js.

    App blocks look like: `  appKey: { ... },` and end with the next `  },`
    at the same indentation level. We find the start, then walk to the matching
    close brace.
    """
    marker = f"  {app_key}: {{"
    start = shell_text.find(marker)
    assert start != -1, f"App section '{app_key}' not found in shell.js"
    # Walk to the matching closing brace at indent level 2
    depth = 0
    i = start + len(marker) - 1  # position at the opening brace
    while i < len(shell_text):
        ch = shell_text[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                # Check if next chars are " ," indicating end of this app block
                rest = shell_text[i+1:i+5]
                if rest.startswith(","):
                    return shell_text[start:i+1]
        i += 1
    return shell_text[start:]


def test_expenses_moved_to_billing_nav():
    """Expenses nav item must be in the Billing app, not the Reports app."""
    shell = (PROJECT_ROOT / "app" / "static" / "js" / "core" / "shell.js").read_text()

    billing_section = _extract_app_section(shell, "billing")
    assert "/bills/expenses" in billing_section, \
        "Billing app nav must include /bills/expenses"
    assert "/bills/cash-buckets" in billing_section, \
        "Billing app nav must include /bills/cash-buckets"

    reports_section = _extract_app_section(shell, "reports")
    assert "/reports/expenses" not in reports_section, \
        "Reports app nav must NOT include /reports/expenses (moved to billing)"
    assert "/reports/cash-buckets" not in reports_section, \
        "Reports app nav must NOT include /reports/cash-buckets (moved to billing)"


def test_old_expenses_route_redirects():
    """Old URL /reports/expenses must redirect to /bills/expenses."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "expenses-page.js").read_text()
    assert "route('/reports/expenses'" in js, \
        "Old /reports/expenses route must still exist as a redirect"
    assert "window.location.hash = '#/bills/expenses'" in js, \
        "Old /reports/expenses must redirect to /bills/expenses"
    assert "route('/bills/expenses'" in js, \
        "New /bills/expenses route must be registered"


def test_old_cash_buckets_route_redirects():
    """Old URL /reports/cash-buckets must redirect to /bills/cash-buckets."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "cash-buckets-page.js").read_text()
    assert "route('/reports/cash-buckets'" in js, \
        "Old /reports/cash-buckets route must still exist as a redirect"
    assert "window.location.hash = '#/bills/cash-buckets'" in js, \
        "Old /reports/cash-buckets must redirect to /bills/cash-buckets"
    assert "route('/bills/cash-buckets'" in js, \
        "New /bills/cash-buckets route must be registered"


def test_action_expense_navigates_to_billing():
    """The __action_expense command-palette action must navigate to billing/expenses."""
    shell = (PROJECT_ROOT / "app" / "static" / "js" / "core" / "shell.js").read_text()
    assert "#/bills/expenses" in shell, \
        "__action_expense must navigate to #/bills/expenses"


def test_no_duplicate_ai_auditor_in_reports_nav():
    """Reports app must not have duplicate 'AI Auditor' nav items.

    Previously there were two: 'Audit Trail' (label=T) and 'AI Auditor' (label=A),
    both pointing to /reports/audit. Now there should be exactly one item with
    label='AI Auditor' that matches the actual page title.
    """
    shell = (PROJECT_ROOT / "app" / "static" / "js" / "core" / "shell.js").read_text()
    reports_section = _extract_app_section(shell, "reports")
    # Count "AI Auditor" labels in the reports section
    count = reports_section.count("label: 'AI Auditor'")
    assert count == 1, f"Expected exactly 1 'AI Auditor' label in reports nav, got {count}"
    # "Audit Trail" label should not exist anymore (renamed to AI Auditor)
    assert "label: 'Audit Trail'" not in reports_section, \
        "'Audit Trail' label should be renamed to 'AI Auditor'"


def test_agent_chat_height_uses_dvh():
    """agent-chat must use calc(100dvh - 18rem) for height (v8.4: was 4rem, now 18rem)."""
    js = (PROJECT_ROOT / "app" / "static" / "js" / "pages" / "agent-chat-page.js").read_text()
    assert "calc(100dvh - 18rem)" in js, \
        "agent-chat must use calc(100dvh - 18rem) for height"
    assert "max-height:500px" not in js and "max-height: 500px" not in js, \
        "agent-chat must not have max-height:500px constraint"


def test_shell_root_full_width():
    """shell-root and .shell-content must have width:100% so content uses full viewport.

    Note: #page is intentionally NOT given padding/width here because #page is
    also used in fullscreen launcher mode (where it should have no padding) and
    kiosk mode. Only .shell-content (which is #page in shell mode) gets the
    padding + width treatment.
    """
    css = (PROJECT_ROOT / "app" / "static" / "css" / "shell.css").read_text()
    # .shell-root must have width: 100%
    shell_root_block = css.split(".shell-root {")[1].split("}")[0]
    assert "width: 100%" in shell_root_block, \
        ".shell-root must have width: 100%"

    # .shell-content must have width: 100%
    page_block = css.split(".shell-content {")[1].split("}")[0]
    assert "width: 100%" in page_block, \
        ".shell-content must have width: 100%"


def test_launcher_uses_full_width():
    """launcher-content must not have max-width: 1200px constraint."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "launcher.css").read_text()
    assert "max-width: 1200px" not in css, \
        ".launcher-content must not have max-width: 1200px (was constraining content)"


def test_modal_overlay_padding_removed():
    """modal-overlay must not have padding (was creating whitespace around modal)."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "design-system.css").read_text()
    # Find the .modal-overlay block
    overlay_block = css.split(".modal-overlay {")[1].split("}")[0]
    assert "padding: 0" in overlay_block, \
        ".modal-overlay must have padding: 0 (was var(--space-4))"


def test_modal_max_width_increased():
    """modal max-width must be larger than the old 500px constraint."""
    css = (PROJECT_ROOT / "app" / "static" / "css" / "design-system.css").read_text()
    modal_block = css.split(".modal {")[1].split("}")[0]
    assert "max-width: 500px" not in modal_block, \
        ".modal must not have max-width: 500px (was too narrow)"
    assert "max-width: 760px" in modal_block or "max-width: 800px" in modal_block, \
        ".modal must have a wider max-width (760px or larger)"


if __name__ == "__main__":
    test_router_isNavActive_excludes_appRoute(); print("OK router isNavActive excludes appRoute")
    test_expenses_moved_to_billing_nav(); print("OK expenses moved to billing nav")
    test_old_expenses_route_redirects(); print("OK old expenses route redirects")
    test_old_cash_buckets_route_redirects(); print("OK old cash-buckets route redirects")
    test_action_expense_navigates_to_billing(); print("OK action_expense navigates to billing")
    test_no_duplicate_ai_auditor_in_reports_nav(); print("OK no duplicate AI Auditor")
    test_agent_chat_height_uses_dvh(); print("OK agent-chat uses dvh")
    test_shell_root_full_width(); print("OK shell-root full width")
    test_launcher_uses_full_width(); print("OK launcher full width")
    test_modal_overlay_padding_removed(); print("OK modal-overlay padding removed")
    test_modal_max_width_increased(); print("OK modal max-width increased")
    print("\nALL v8.3 SIDEBAR/RELOCATION TESTS PASSED")
