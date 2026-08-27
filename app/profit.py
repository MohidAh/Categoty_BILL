"""v7.0 Phase 1 — profit.py SHIM (backward compatibility).

This module re-exports all public names from the three split modules so that
existing imports (`from .profit import X`, `from . import profit; profit.X`)
continue to work without any changes elsewhere.

The actual implementations live in:
  - profit_engine.py: state primitives, rebuild, peek_avg_cost, log_state_drift
  - profit_analytics.py: margins, monthly, YTD, dashboard, daily stock
  - profit_cash.py: cash buckets, stock reserve, owner withdrawals
"""
from .profit_engine import (
    log_state_drift,
    apply_purchase_to_state,
    apply_sale_to_state,
    peek_avg_cost,
    reverse_sale_in_state,
    apply_adjustment_to_state,
    rebuild_stock_state,
    get_category_stock_state,
    _get_setting,
)
from .profit_analytics import (
    get_margins,
    get_monthly_profit,
    get_ytd_profit,
    get_store_profit_dashboard,
    get_daily_stock_report,
)
from .profit_cash import (
    get_cash_buckets,
    get_stock_reserve,
    add_owner_withdrawal,
    list_owner_withdrawals,
    get_owner_withdrawals_summary,
)

__all__ = [
    # Engine
    "log_state_drift", "apply_purchase_to_state", "apply_sale_to_state",
    "peek_avg_cost", "reverse_sale_in_state", "apply_adjustment_to_state",
    "rebuild_stock_state", "get_category_stock_state", "_get_setting",
    # Analytics
    "get_margins", "get_monthly_profit", "get_ytd_profit",
    "get_store_profit_dashboard", "get_daily_stock_report",
    # Cash
    "get_cash_buckets", "get_stock_reserve", "add_owner_withdrawal",
    "list_owner_withdrawals", "get_owner_withdrawals_summary",
]
