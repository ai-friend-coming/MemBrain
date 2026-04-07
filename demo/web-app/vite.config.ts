import type { Plugin } from 'vite'
import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
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

  return {
    plugins: [tailwindcss(), vue(), cacheHeadersPlugin()],
    envDir: '..',
    envPrefix: ['VITE_', 'LLM_'],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      allowedHosts: env.ALLOWED_HOSTS.split(',').map(h => h.trim()),
      proxy: {
        '/api': {
          target: `http://localhost:${env.BACKEND_PORT}`,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: Number(env.WEB_PREVIEW_PORT),
      allowedHosts: env.ALLOWED_HOSTS.split(',').map(h => h.trim()),
      proxy: {
        '/api': {
          target: `http://localhost:${env.BACKEND_PORT}`,
          changeOrigin: true,
        },
      },
    },
  }
})
