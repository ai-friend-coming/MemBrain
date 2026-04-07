<script setup lang="ts">
import DatasetViewer from '@/views/DatasetViewer.vue'
import { Toaster } from '@/components/ui/sonner'
</script>

<template>
  <div class="app-wrapper">
    <div class="app-container">
      <DatasetViewer />
    </div>
  </div>
  <Toaster />
</template>

<style scoped>
.app-wrapper {
  width: 100vw;
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.app-container {
  flex: 1;
  overflow: hidden;
  display: flex;
}
</style>
