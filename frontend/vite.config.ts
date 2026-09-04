import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const RC_PAGINATION_ENTRY = '/@rc-component/pagination/es/Pagination.js'
const RC_PICKER_MASK_FORMAT_ENTRY = '/@rc-component/picker/es/PickerInput/Selector/MaskFormat.js'

function publicEnglishDependencyBundle(): Plugin {
  const upstreamPickerMaskKey = String.fromCharCode(0x9867)

  return {
    name: 'public-english-dependency-bundle',
    enforce: 'pre',
    transform(code, id) {
      const moduleId = id.replace(/\\/g, '/').split('?', 1)[0]

      if (moduleId.endsWith(RC_PAGINATION_ENTRY)) {
        const chineseFallback = 'import zhCN from "./locale/zh_CN";'
        const englishFallback = 'import zhCN from "./locale/en_US";'
        if (!code.includes(chineseFallback)) {
          throw new Error('Unexpected rc-pagination locale module shape')
        }
        return { code: code.replace(chineseFallback, englishFallback), map: null }
      }

      if (moduleId.endsWith(RC_PICKER_MASK_FORMAT_ENTRY)) {
        const chineseSentinel = `var REPLACE_KEY = '${upstreamPickerMaskKey}';`
        const asciiSentinel =
          'var REPLACE_KEY = String.fromCharCode(new Uint16Array([0x9867])[0]);'
        if (!code.includes(chineseSentinel)) {
          throw new Error('Unexpected rc-picker mask module shape')
        }
        return { code: code.replace(chineseSentinel, asciiSentinel), map: null }
      }

      return null
    },
  }
}

export default defineConfig({
  plugins: [publicEnglishDependencyBundle(), react()],
  server: {
    port: 3101,
    proxy: {
      '/api': {
        target: 'http://localhost:3100',
        changeOrigin: true,
      },
      '/artifacts': {
        target: 'http://localhost:3100',
        changeOrigin: true,
      },
    },
  },
})
