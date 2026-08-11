<template>
  <button @click="loadAcl">Load ACL</button>
</template>

<script setup lang="ts">
import { GET_NETWORK_ACL_PATH } from "../api/aclApi";

async function loadAcl() {
  await useAxios({
    url: GET_NETWORK_ACL_PATH,
    method: "GET",
  });
}
</script>
