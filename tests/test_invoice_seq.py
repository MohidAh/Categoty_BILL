"""FIX 2.2: Invoice sequence tests — gapless, sequential, no duplicates."""
import sys, os, tempfile, shutil
from test_helpers import setup_test_db, cleanup
sys.path.insert(0, '.')



def test_gapless_sequence():
    """Create 5 invoice numbers → assert gapless sequential."""
    test_dir = setup_test_db()
    try:
        from app import shop
        numbers = [shop.get_next_invoice_no() for _ in range(5)]
        # Extract numeric parts
        nums = [int(n.replace("INV-", "")) for n in numbers]
        # Assert sequential
        for i in range(1, 5):
            assert nums[i] == nums[i-1] + 1, f"Gap detected: {nums[i-1]} → {nums[i]}"
        # Assert no duplicates
        assert len(set(numbers)) == 5, f"Duplicate invoice numbers: {numbers}"
        print(f"✓ test_gapless_sequence: {numbers}")
    finally:
        cleanup(test_dir)

def test_no_duplicates_under_concurrency():
    """Call get_next_invoice_no 20 times → all unique."""
    test_dir = setup_test_db()
    try:
        from app import shop
        numbers = [shop.get_next_invoice_no() for _ in range(20)]
        assert len(set(numbers)) == 20, f"Found duplicates in 20 calls"
        print(f"✓ test_no_duplicates_under_concurrency: 20 unique numbers")
    finally:
        cleanup(test_dir)

if __name__ == "__main__":
    test_gapless_sequence()
    test_no_duplicates_under_concurrency()
    print("\n✅ ALL INVOICE SEQ TESTS PASSED")
