def create_customer_with_usd(client, amount="1000.00"):
    customer_id = client.post("/customers").json()["customer_id"]
    client.post(f"/customers/{customer_id}/balance-credits", json={"currency": "USD", "amount": amount})
    return customer_id
