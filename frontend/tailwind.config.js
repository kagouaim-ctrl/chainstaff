/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      // 与 src/index.css 的 CSS 变量保持一致；这里用十六进制，透明度写法（如 bg-up/10）才能生效
      colors: {
        // fomo 实测值（见 docs/PLAN.md 与 fomo-research），不要改成近似值
        bg: '#060510',
        panel: '#12111A',
        raised: '#161522',
        line: 'rgba(203,208,235,0.1)',
        fg: '#F7F7F7',
        sub: '#9899A3',
        mute: '#474B52',
        agent: '#516AF6',
        up: '#21C95E',
        down: '#FF622E',
        paper: '#f5b544',
        live: '#38bdf8',
        warn: '#f59e0b',
      },
      fontFamily: {
        // fomo 用 Aeonik（商业授权字体），这里用免费可商用的 Inter Tight 代替；有授权后把 Aeonik 放到第一位即可
        sans: ['Aeonik', '"Inter Tight"', '"Noto Sans SC"', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: { card: '12px' },
    },
  },
  plugins: [],
}
