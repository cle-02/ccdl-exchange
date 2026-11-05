"""Pure matching calculations; no AWS calls."""


def match_pair(incoming, resting):
    """Return one proposed fill, or None if prices/sides do not match."""
    for order in (incoming, resting):
        if order["Side"] not in ("BUY", "SELL"):
            raise ValueError("Invalid order side")

        for field in ("PriceTicks", "RemainingLots"):
            value = order[field]
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field} must be a positive integer")

    if incoming["OrderID"] == resting["OrderID"]:
        raise ValueError("Cannot match an order against itself")

    if incoming["Symbol"] != resting["Symbol"]:
        return None

    if incoming["Side"] == resting["Side"]:
        return None

    buy = incoming if incoming["Side"] == "BUY" else resting
    sell = incoming if incoming["Side"] == "SELL" else resting

    if buy["PriceTicks"] < sell["PriceTicks"]:
        return None

    quantity = min(incoming["RemainingLots"], resting["RemainingLots"])

    return {
        "Symbol": incoming["Symbol"],
        "BuyOrderID": buy["OrderID"],
        "SellOrderID": sell["OrderID"],
        "PriceTicks": resting["PriceTicks"],
        "QuantityLots": quantity,
        "IncomingRemainingLots": incoming["RemainingLots"] - quantity,
        "RestingRemainingLots": resting["RemainingLots"] - quantity,
    }


def select_best_order(incoming, resting_orders):
    """Select the best eligible order from a supplied book snapshot."""
    eligible = []

    for resting in resting_orders:
        if resting["RemainingLots"] == 0:
            continue

        if match_pair(incoming, resting) is None:
            continue

        sequence = resting.get("Sequence")
        if type(sequence) is not int or sequence < 0:
            raise ValueError("Eligible resting orders need a valid Sequence")

        eligible.append(resting)

    if not eligible:
        return None

    sequences = [order["Sequence"] for order in eligible]
    if len(sequences) != len(set(sequences)):
        raise ValueError("Eligible orders must have unique sequences")

    def priority(order):
        price = order["PriceTicks"]
        if incoming["Side"] == "SELL":
            price = -price
        return (price, order["Sequence"])

    return min(eligible, key=priority)


def match_order_book(incoming, resting_orders):
    """Calculate fills on copies of the supplied orders; no AWS writes."""
    from copy import deepcopy

    incoming_copy = deepcopy(incoming)
    book_copy = deepcopy(resting_orders)
    fills = []

    remaining = incoming_copy["RemainingLots"]
    if type(remaining) is not int or remaining <= 0:
        raise ValueError("Incoming RemainingLots must be a positive integer")

    ids = [order["OrderID"] for order in book_copy]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate resting OrderID")
    if incoming_copy["OrderID"] in ids:
        raise ValueError("Incoming order is already in the resting book")

    while incoming_copy["RemainingLots"] > 0:
        resting = select_best_order(incoming_copy, book_copy)
        if resting is None:
            break

        fill = match_pair(incoming_copy, resting)
        fills.append(fill)

        incoming_copy["RemainingLots"] = fill["IncomingRemainingLots"]
        resting["RemainingLots"] = fill["RestingRemainingLots"]

    def status(order):
        if order["RemainingLots"] == 0:
            return "FILLED"
        if order["RemainingLots"] < order["QuantityLots"]:
            return "PARTIALLY_FILLED"
        return "OPEN"

    incoming_copy["Status"] = status(incoming_copy)
    for order in book_copy:
        order["Status"] = status(order)

    return {
        "IncomingOrder": incoming_copy,
        "RestingOrders": book_copy,
        "Fills": fills,
    }
