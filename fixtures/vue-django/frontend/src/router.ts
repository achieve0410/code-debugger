import { createRouter, createWebHistory } from "vue-router";
import HomePage from "./HomePage.vue";
import DetailsPage from "./DetailsPage.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", component: HomePage },
    { path: "/details", component: DetailsPage },
  ],
});
