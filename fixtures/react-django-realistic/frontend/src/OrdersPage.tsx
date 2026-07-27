import { useEffect, useState } from "react";

export function OrdersPage() {
  const [orders, setOrders] = useState([]);

  async function loadOrders() {
    const response = await fetch("/api/orders/");
    setOrders(await response.json());
  }

  async function createOrder() {
    const draft = { customerId: 1, note: "manual order" };
    await fetch("/api/orders/", {
      method: "POST",
      body: JSON.stringify(draft),
    });
    await loadOrders();
  }

  useEffect(() => {
    loadOrders();
  }, []);

  return (
    <section>
      <h2>Orders ({orders.length})</h2>
      <button onClick={createOrder}>Create order</button>
    </section>
  );
}
