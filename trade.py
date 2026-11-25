import json
import math
from pathlib import Path

import pandas as pd


BASE_DIR = Path(__file__).resolve().parent


# --------------------------------------------------
# 1. READ TRADE CSV
# --------------------------------------------------

def read_trade_data(csv_path=None):
    path = Path(csv_path) if csv_path is not None else BASE_DIR / "trades_test.csv"
    df = pd.read_csv(path)

    required_columns = [
        "portfolio_id",
        "security_id",
        "action",
        "quantity",
        "price",
        "security_type"
    ]

    # Check whether all required columns exist
    if not all(col in df.columns for col in required_columns):
        raise ValueError("CSV is missing required columns.")

    return df


def load_portfolio_reference_data(json_path=None):
    path = (
        Path(json_path)
        if json_path is not None
        else BASE_DIR / "portfolio_reference_data.json"
    )

    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as portfolio_file:
        data = json.load(portfolio_file)

    if not isinstance(data, dict):
        raise ValueError("Portfolio reference data must be a JSON object.")

    normalized = {}
    for portfolio_id, portfolio in data.items():
        normalized[portfolio_id] = normalize_portfolio(portfolio)

    return normalized


def normalize_portfolio(portfolio):
    normalized = dict(portfolio)

    max_position_pct = float(normalized["max_position_pct"])
    if max_position_pct > 1:
        max_position_pct /= 100
    normalized["max_position_pct"] = max_position_pct

    normalized["restricted_securities"] = [
        str(security).strip()
        for security in normalized.get("restricted_securities", [])
        if str(security).strip()
    ]

    holdings = []
    for holding in normalized.get("current_holdings", []):
        if isinstance(holding, dict):
            security_id = holding.get("security_id")
            quantity = holding.get("quantity")
            market_price = holding.get("market_price")
        else:
            if len(holding) != 3:
                raise ValueError("Invalid holding data.")
            security_id, quantity, market_price = holding

        security_id = str(security_id).strip() if security_id is not None else ""
        if not security_id:
            raise ValueError("Invalid holding data.")

        holdings.append({
            "security_id": security_id,
            "quantity": float(quantity),
            "market_price": float(market_price)
        })

    normalized["current_holdings"] = holdings
    normalized["cash_balance"] = float(normalized["cash_balance"])
    return normalized


# --------------------------------------------------
# 2. TAKE PORTFOLIO REFERENCE DATA FROM USER
# --------------------------------------------------

def get_portfolio_data(portfolio_ids):

    portfolio_data = {}

    for portfolio_id in portfolio_ids:

        print("\n--------------------------------")
        print("Enter details for:", portfolio_id)
        print("--------------------------------")

        cash_balance = float(input("Enter cash balance: "))

        if cash_balance < 0:
            raise ValueError("Cash balance cannot be negative.")

        max_position_pct = float(
            input("Enter maximum position percentage (e.g. 25): ")
        ) / 100

        if not 0 <= max_position_pct <= 1:
            raise ValueError("Maximum position percentage must be between 0 and 100.")

        # Restricted securities
        count = int(input("Number of restricted securities: "))

        if count < 0:
            raise ValueError("Count cannot be negative.")

        restricted_securities = []

        for i in range(count):
            security = input(
                f"Enter restricted security {i + 1}: "
            ).strip()

            if not security:
                raise ValueError("Security ID cannot be empty.")

            restricted_securities.append(security)

        # Current holdings
        holding_count = int(input("Number of current holdings: "))

        if holding_count < 0:
            raise ValueError("Holding count cannot be negative.")

        current_holdings = []

        for i in range(holding_count):

            print(f"\nHolding {i + 1}")

            security_id = input("Security ID: ").strip()
            quantity = float(input("Quantity: "))
            market_price = float(input("Market price: "))

            if not security_id or quantity < 0 or market_price < 0:
                raise ValueError("Invalid holding data.")

            current_holdings.append({
                "security_id": security_id,
                "quantity": quantity,
                "market_price": market_price
            })

        portfolio_data[portfolio_id] = {
            "cash_balance": cash_balance,
            "restricted_securities": restricted_securities,
            "max_position_pct": max_position_pct,
            "current_holdings": current_holdings
        }

    return portfolio_data


# --------------------------------------------------
# 3. PROCESS ONE TRADE
# --------------------------------------------------

def check_trade(trade, portfolio):
    portfolio = normalize_portfolio(portfolio)

    try:
        quantity = float(trade["quantity"])
        price = float(trade["price"])
    except (TypeError, ValueError, KeyError):
        return "Rejected", "Invalid trade data."

    action = trade["action"]
    security_id = trade["security_id"]

    # STEP 1: VALIDATE TRADE DATA

    if (
        pd.isna(quantity)
        or pd.isna(price)
        or not math.isfinite(quantity)
        or not math.isfinite(price)
        or quantity <= 0
        or price <= 0
        or action not in ["Buy", "Sell"]
    ):
        return "Rejected", "Invalid trade data."

    # STEP 2: RESTRICTED SECURITY CHECK

    if security_id in portfolio["restricted_securities"]:
        return "Rejected", "Security is restricted for this portfolio."

    # Find existing holding of this security

    existing_quantity = sum(
        holding["quantity"]
        for holding in portfolio["current_holdings"]
        if holding["security_id"] == security_id
    )

    # STEP 3: SELL CHECK

    if action == "Sell":

        if existing_quantity < quantity:
            return "Rejected", "Insufficient holdings to sell."

        # Sell never triggers concentration review
        return "Approved", "Trade passed compliance checks."

    # STEP 4: BUY AFFORDABILITY CHECK

    trade_cost = quantity * price

    if trade_cost > portfolio["cash_balance"]:
        return "Rejected", "Insufficient cash for this buy order."

    # STEP 5: BUY POSITION CONCENTRATION CHECK

    post_trade_cash = portfolio["cash_balance"] - trade_cost

    post_trade_quantity = existing_quantity + quantity

    post_trade_security_value = post_trade_quantity * price

    # Value all OTHER securities using their stored market prices

    value_of_other_holdings = sum(
        holding["quantity"] * holding["market_price"]
        for holding in portfolio["current_holdings"]
        if holding["security_id"] != security_id
    )

    total_portfolio_value_after = (
        post_trade_cash
        + post_trade_security_value
        + value_of_other_holdings
    )

    if total_portfolio_value_after <= 0:
        return "Rejected", "Invalid portfolio valuation."

    position_pct = (
        post_trade_security_value / total_portfolio_value_after
    )

    if position_pct > portfolio["max_position_pct"]:

        return (
            "Needs Manual Sign-off",
            "Exceeds maximum position concentration — requires approval."
        )

    # STEP 6: OTHERWISE APPROVED

    return "Approved", "Trade passed compliance checks."


# --------------------------------------------------
# 4. PROCESS ALL TRADES
# --------------------------------------------------

def process_trades(df, portfolio_data):
    decisions = []
    reasons = []

    for _, trade in df.iterrows():

        portfolio_id = trade["portfolio_id"]

        if portfolio_id not in portfolio_data:

            decision = "Rejected"
            reason = "Portfolio reference data not found."

        else:

            decision, reason = check_trade(
                trade,
                portfolio_data[portfolio_id]
            )

        decisions.append(decision)
        reasons.append(reason)

    df["decision"] = decisions
    df["reason"] = reasons

    return df


# --------------------------------------------------
# 5. MAIN FUNCTION
# --------------------------------------------------

def main():

    print("======================================")
    print("PORTFOLIO TRADE COMPLIANCE CHECKER")
    print("======================================")

    # Read input CSV
    df = read_trade_data()

    print("\nTotal trades:", len(df))

    # Get unique portfolio IDs
    portfolio_ids = df["portfolio_id"].dropna().unique()

    print("\nPortfolios found:", list(portfolio_ids))

    # Use supplied reference data when available; fall back to interactive entry
    portfolio_data = load_portfolio_reference_data()
    missing_portfolio_ids = [
        portfolio_id
        for portfolio_id in portfolio_ids
        if portfolio_id not in portfolio_data
    ]
    if missing_portfolio_ids:
        portfolio_data.update(get_portfolio_data(missing_portfolio_ids))

    # Process trades
    result = process_trades(df, portfolio_data)

    # Save output
    output_path = BASE_DIR / "trade_output.csv"
    result.to_csv(output_path, index=False)

    print("\n======================================")
    print("PROCESSING COMPLETED")
    print("======================================")

    print(result[
        ["portfolio_id", "security_id", "action", "decision", "reason"]
    ].to_string(index=False))

    print(f"\nOutput saved to {output_path}")


if __name__ == "__main__":
    main()
