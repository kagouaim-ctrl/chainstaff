/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // 与 src/index.css 的 CSS 变量保持一致；这里用十六进制，透明度写法（如 bg-up/10）才能生效
      colors: {
        bg: '#0b0d10',
        panel: '#14171c',
        raised: '#1a1e24',
        line: '#232830',
        fg: '#e9ebee',
        sub: '#7c8591',
        up: '#22c38e',
        down: '#f25c6a',
        agent: '#6e8bff',
        paper: '#f5b544',
        live: '#38bdf8',
        warn: '#f59e0b',
      },
      fontFamily: {
        sans: ['"Geist Variable"', '"Noto Sans SC"', 'system-ui', 'sans-serif'],
        mono: ['"Geist Variable"', 'ui-monospace', 'monospace'],
      },
      borderRadius: { card: '12px' },
    },
  },
  plugins: [],
}
