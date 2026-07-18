import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Set base to repo name for GitHub Pages deployment.
  // Change "TaskForge" to your actual GitHub repo name if different.
  base: '/',
})
