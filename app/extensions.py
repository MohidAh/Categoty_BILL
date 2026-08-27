"""v7.2 Phase 1 — extensions.py SHIM (backward compatibility).

This module re-exports all public names from the three split modules so that
existing imports continue to work without any changes.

The actual implementations live in:
  - ext_pos.py: bundles, happy-hour, lost-sales, break-even, margin alerts, forecast
  - ext_intel.py: trends, automation, season-prep, closed-days, seasons
  - ext_comm.py: urdhaar, customer groups, broadcast, WhatsApp parse, Raast
"""
from .ext_pos import (
    list_bundles, create_bundle, delete_bundle, get_bundle_sell_price_allocation,
    list_price_rules, create_price_rule, delete_price_rule, get_active_happy_hour_discount,
    log_lost_sale, get_lost_sales_summary,
    get_break_even, get_margin_alerts, get_cash_flow_forecast,
)
from .ext_intel import (
    get_internal_trend_signals,
    check_auto_confirm_bills, check_recurring_detection,
    prepare_for_season,
    list_closed_days, add_closed_day, remove_closed_day,
    list_seasons, add_season,
)
from .ext_comm import (
    get_urdhaar_reminders,
    get_customer_groups, get_broadcast_list,
    parse_whatsapp_order,
    get_raast_reconciliation,
)

__all__ = [
    # POS mechanics
    "list_bundles", "create_bundle", "delete_bundle", "get_bundle_sell_price_allocation",
    "list_price_rules", "create_price_rule", "delete_price_rule", "get_active_happy_hour_discount",
    "log_lost_sale", "get_lost_sales_summary",
    "get_break_even", "get_margin_alerts", "get_cash_flow_forecast",
    # Intelligence
    "get_internal_trend_signals",
    "check_auto_confirm_bills", "check_recurring_detection",
    "prepare_for_season",
    "list_closed_days", "add_closed_day", "remove_closed_day",
    "list_seasons", "add_season",
    # Communication
    "get_urdhaar_reminders",
    "get_customer_groups", "get_broadcast_list",
    "parse_whatsapp_order",
    "get_raast_reconciliation",
]
