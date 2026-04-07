import type { Plugin } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'

import UnoCSS from 'unocss/vite'
import { defineConfig, loadEnv } from 'vite'

function cacheHeadersPlugin(): Plugin {
  return {
    name: 'cache-headers',
    configurePreviewServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.startsWith('/assets/')) {
          // Content-hashed filenames are immutable — cache for 1 year
          res.setHeader('Cache-Control', 'public, max-age=31536000, immutable')
        }
        else {
          // index.html and other entry points must revalidate on every load
          res.setHeader('Cache-Control', 'no-cache')
        }
        next()
      })
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', '')

  const allowedHosts = (env.ALLOWED_HOSTS ?? 'localhost').split(',').map(h => h.trim())
  const backendTarget = `http://localhost:${env.BACKEND_PORT ?? 9574}`

  const alias = [{ find: '@', replacement: fileURLToPath(new URL('./src', import.meta.url)) }]

  const apiProxy = { '/api': { target: backendTarget, changeOrigin: true } }

  return {
    plugins: [UnoCSS(), vue(), cacheHeadersPlugin()],
    resolve: { alias },
    server: {
      host: true,
      allowedHosts,
      proxy: apiProxy,
    },
    preview: {
      port: Number(env.WEB_PORT ?? 5173),
      allowedHosts,
      proxy: apiProxy,
    },
  }
})
