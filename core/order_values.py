"""Pure order-value helpers shared by storage and presentation layers."""

REVENUE_STATUSES = ("paid", "paid_waiting_email")
JUNK_STATUSES = ("cancelled", "cancelled_timeout")


def order_revenue(order: dict) -> int:
    """Return the full amount paid, including the wallet part of partial orders."""
    return int(order.get("original_total") or order.get("total") or 0)


def order_cost(order: dict) -> int:
    """Return recorded supplier cost; explicit zero means confirmed no cost."""
    return int(order.get("cost") or 0)


def holds_unrefunded_wallet_money(order: dict) -> bool:
    """True khi ví khách đã bị trừ cho đơn này mà chưa được hoàn lại.

    Đơn `failed` nghĩa là khách đã trả tiền nhưng không nhận được hàng. Với đơn
    trả bằng ví, khoản tiền đó nằm trong sổ ví của bot và **bản ghi đơn là bằng
    chứng duy nhất** — xoá đơn là mất dấu nghĩa vụ hoàn tiền.
    """
    paid_from_wallet = bool(order.get("wallet_payment_confirmed")) or int(
        order.get("wallet_paid", 0) or 0
    ) > 0
    if not paid_from_wallet:
        return False
    return not (order.get("wallet_refunded") or order.get("refund_credited"))
