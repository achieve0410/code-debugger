import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

export function OrderDetailPage() {
  const { orderId } = useParams();
  const navigate = useNavigate();
  const [order, setOrder] = useState(null);

  async function loadOrder() {
    const response = await fetch(`/api/orders/${orderId}/`);
    setOrder(await response.json());
  }

  async function cancelOrder() {
    await fetch(`/api/orders/${orderId}/cancel/`, { method: "POST" });
    await loadOrder();
  }

  async function removeOrder() {
    await fetch(`/api/orders/${orderId}/`, { method: "DELETE" });
    navigate("/orders");
  }

  useEffect(() => {
    loadOrder();
  }, [orderId]);

  return (
    <article>
      <h2>Order detail</h2>
      <button onClick={cancelOrder}>Cancel</button>
      <button onClick={removeOrder}>Delete</button>
    </article>
  );
}
