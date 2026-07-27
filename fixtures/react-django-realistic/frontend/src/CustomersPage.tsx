import { useEffect, useState } from "react";

export function CustomersPage() {
  const [customers, setCustomers] = useState([]);

  async function loadCustomers() {
    const response = await fetch("/api/customers/");
    setCustomers(await response.json());
  }

  useEffect(() => {
    loadCustomers();
  }, []);

  return (
    <section>
      <h2>Customers ({customers.length})</h2>
    </section>
  );
}
