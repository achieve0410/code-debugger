<template>
  <section>
    <h2>Orders ({{ orders?.length ?? 0 }})</h2>
    <button @click="createOrder">Create order</button>
  </section>
</template>

<script setup lang="ts">
const { data: orders, refresh } = await useFetch("/api/orders/");

async function createOrder() {
  const draft = { itemId: 1, quantity: 2 };
  await $fetch("/api/orders/", {
    method: "POST",
    body: draft,
  });
  await refresh();
}
</script>
