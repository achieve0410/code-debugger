<template>
  <article>
    <h2>Order detail</h2>
    <button @click="cancelOrder">Cancel</button>
    <NuxtLink to="/">Home</NuxtLink>
  </article>
</template>

<script setup lang="ts">
const route = useRoute();
const orderId = route.params.id;

const { data: order, refresh } = await useFetch(`/api/orders/${orderId}/`);

async function cancelOrder() {
  await $fetch(`/api/orders/${orderId}/cancel/`, { method: "POST" });
  await refresh();
}
</script>
