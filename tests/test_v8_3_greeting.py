"""v8.3 — Tests for agent greeting/small-talk detection.

When the user types "hey", "hello", "salam", etc., the agent should NOT
call any business-data tools. It should return a friendly greeting with
starter follow-up suggestions instead.
"""
import os, sys, tempfile, shutil
from pathlib import Path
from test_helpers import setup_test_db, cleanup
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))



def test_hey_returns_greeting_without_tool_calls():
    """The original bug: 'hey' was triggering get_margins + get_monthly_profit."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("hey")
        assert r["tool_trace"] == [], f"Expected empty tool_trace, got: {r['tool_trace']}"
        assert "margin" not in r["answer"].lower() or "%" not in r["answer"], \
            f"Answer should not contain margin data, got: {r['answer']}"
        assert len(r["suggested_followups"]) >= 2, "Should have follow-up suggestions"
    finally:
        cleanup(test_dir)


def test_hello_does_not_trigger_tools():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("hello")
        assert r["tool_trace"] == []
        assert "BillBook AI Assistant" in r["answer"] or "ready" in r["answer"].lower()
    finally:
        cleanup(test_dir)


def test_salam_urdu_greeting():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("salam")
        assert r["tool_trace"] == []
    finally:
        cleanup(test_dir)


def test_assalam_o_alaikum():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("assalam o alaikum")
        assert r["tool_trace"] == []
    finally:
        cleanup(test_dir)


def test_thank_you():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("thanks")
        assert r["tool_trace"] == []
        assert "welcome" in r["answer"].lower()
    finally:
        cleanup(test_dir)


def test_who_are_you():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("who are you")
        assert r["tool_trace"] == []
        assert "AI Assistant" in r["answer"]
    finally:
        cleanup(test_dir)


def test_bye():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("bye")
        assert r["tool_trace"] == []
        assert "goodbye" in r["answer"].lower() or "here whenever" in r["answer"].lower()
    finally:
        cleanup(test_dir)


def test_margin_question_still_calls_tools():
    """Make sure we didn't break the actual margin question flow."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("What is my actual overall margin?")
        assert len(r["tool_trace"]) > 0, "Margin question should call tools"
        assert any("get_margins" in str(s) for s in r["tool_trace"])
    finally:
        cleanup(test_dir)


def test_cash_question_still_calls_tools():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("How much cash can I safely withdraw?")
        assert len(r["tool_trace"]) > 0, "Cash question should call tools"
        assert any("get_cash_buckets" in str(s) for s in r["tool_trace"])
    finally:
        cleanup(test_dir)


def test_credit_question_still_calls_tools():
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("Which customers have outstanding credit?")
        assert len(r["tool_trace"]) > 0, "Credit question should call tools"
        assert any("get_customer_credit_top" in str(s) for s in r["tool_trace"])
    finally:
        cleanup(test_dir)


def test_non_greeting_long_question_does_not_match():
    """Make sure long questions containing 'hey' as a substring don't match."""
    test_dir = setup_test_db()
    try:
        from app.agent import run_agent
        r = run_agent("hey what is my margin this month")
        # This is a real question, not just "hey" — should call tools
        assert len(r["tool_trace"]) > 0 or "margin" in r["answer"].lower(), \
            "Long question with 'hey' should be treated as real question"
    finally:
        cleanup(test_dir)


if __name__ == "__main__":
    test_hey_returns_greeting_without_tool_calls(); print("OK hey")
    test_hello_does_not_trigger_tools(); print("OK hello")
    test_salam_urdu_greeting(); print("OK salam")
    test_assalam_o_alaikum(); print("OK assalam o alaikum")
    test_thank_you(); print("OK thanks")
    test_who_are_you(); print("OK who are you")
    test_bye(); print("OK bye")
    test_margin_question_still_calls_tools(); print("OK margin question still calls tools")
    test_cash_question_still_calls_tools(); print("OK cash question still calls tools")
    test_credit_question_still_calls_tools(); print("OK credit question still calls tools")
    test_non_greeting_long_question_does_not_match(); print("OK long question not treated as greeting")
    print("\nALL v8.3 GREETING TESTS PASSED")
