<template>
  <main>
    <SummaryCard />
    <button @click="loadItems">Load items</button>
    <button @click="goDetails">Details</button>
    <component :is="dynamicComponent" />
  </main>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import SummaryCard from "./SummaryCard.vue";

const router = useRouter();
const dynamicComponent = ref("RuntimeOnlyPanel");

async function loadItems() {
  await fetch("/api/items/?include=active");
}

function goDetails() {
  router.push("/details");
}
</script>
