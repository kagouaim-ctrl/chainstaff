/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'var(--c-bg)',
        panel: 'var(--c-panel)',
        raised: 'var(--c-raised)',
        line: 'var(--c-line)',
        fg: 'var(--c-fg)',
        sub: 'var(--c-sub)',
        up: 'var(--c-up)',
        down: 'var(--c-down)',
        agent: 'var(--c-agent)',
        paper: 'var(--c-paper)',
        live: 'var(--c-live)',
        warn: 'var(--c-warn)',
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
