import { useNavigate } from "react-router-dom";

export function HomePage() {
  const navigate = useNavigate();

  function openOrders() {
    navigate("/orders");
  }

  return (
    <main>
      <h1>Realistic shop</h1>
      <button onClick={openOrders}>Orders</button>
    </main>
  );
}
