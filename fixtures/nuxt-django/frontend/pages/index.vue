<template>
  <main>
    <h1>Items ({{ items?.length ?? 0 }})</h1>
    <button @click="checkHealth">Health</button>
    <NuxtLink to="/orders">Orders</NuxtLink>
  </main>
</template>

<script setup lang="ts">
const { data: items } = await useFetch("/api/items/");

async function checkHealth() {
  await $fetch("/api/health");
}
</script>
